"""Minimal stdlib-only process boundary for the WeCom native archive SDK.

Do not import application modules, cryptography, database drivers, or HTTP
clients here.  The vendor SDK has native OpenSSL dependencies that conflict
with libraries already loaded by the main AI-CRM Python process.
"""

from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path
from typing import Any


RESULT_PREFIX = "AICRM_SDK_RESULT="
PTR = ctypes.c_void_p


class NativeSdkError(RuntimeError):
    def __init__(self, error_code: str, *, native_code: int = 0) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.native_code = int(native_code)


def _configure(lib_path: str):
    path = Path(str(lib_path or ""))
    if not path.is_file():
        raise NativeSdkError("sdk_library_missing")
    lib = ctypes.CDLL(str(path))
    lib.NewSdk.argtypes = []
    lib.NewSdk.restype = PTR
    lib.Init.argtypes = [PTR, ctypes.c_char_p, ctypes.c_char_p]
    lib.Init.restype = ctypes.c_int
    lib.DestroySdk.argtypes = [PTR]
    lib.DestroySdk.restype = None
    lib.NewSlice.argtypes = []
    lib.NewSlice.restype = PTR
    lib.FreeSlice.argtypes = [PTR]
    lib.FreeSlice.restype = None
    lib.GetChatData.argtypes = [
        PTR,
        ctypes.c_ulonglong,
        ctypes.c_uint,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        PTR,
    ]
    lib.GetChatData.restype = ctypes.c_int
    lib.GetContentFromSlice.argtypes = [PTR]
    lib.GetContentFromSlice.restype = ctypes.c_char_p
    lib.DecryptData.argtypes = [ctypes.c_char_p, ctypes.c_char_p, PTR]
    lib.DecryptData.restype = ctypes.c_int
    return lib


def _slice_json(lib, slice_ptr: PTR) -> dict[str, Any]:
    raw = lib.GetContentFromSlice(slice_ptr)
    parsed = json.loads(raw.decode("utf-8") if raw else "{}")
    if not isinstance(parsed, dict):
        raise NativeSdkError("sdk_payload_invalid")
    return dict(parsed)


def _fetch(request: dict[str, Any]) -> dict[str, Any]:
    lib = _configure(str(request.get("lib_path") or ""))
    sdk_ptr = lib.NewSdk()
    if not sdk_ptr:
        raise NativeSdkError("sdk_pointer_missing")
    slice_ptr = None
    try:
        init_ret = lib.Init(
            sdk_ptr,
            str(request.get("corp_id") or "").encode("utf-8"),
            str(request.get("archive_secret") or "").encode("utf-8"),
        )
        if init_ret != 0:
            raise NativeSdkError("sdk_init_failed", native_code=init_ret)
        slice_ptr = lib.NewSlice()
        if not slice_ptr:
            raise NativeSdkError("sdk_slice_missing")
        fetch_ret = lib.GetChatData(
            sdk_ptr,
            int(request.get("seq") or 0),
            max(1, min(int(request.get("limit") or 100), 1000)),
            None,
            None,
            max(1, int(request.get("timeout") or 60)),
            slice_ptr,
        )
        if fetch_ret != 0:
            raise NativeSdkError("sdk_fetch_failed", native_code=fetch_ret)
        payload = _slice_json(lib, slice_ptr)
    finally:
        if slice_ptr:
            lib.FreeSlice(slice_ptr)
        lib.DestroySdk(sdk_ptr)
    return {"ok": True, "payload": payload}


def _decrypt(request: dict[str, Any]) -> dict[str, Any]:
    items = request.get("items")
    if not isinstance(items, list) or len(items) > 1000:
        raise NativeSdkError("sdk_decrypt_items_invalid")
    lib = _configure(str(request.get("lib_path") or ""))
    payloads: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise NativeSdkError("sdk_decrypt_item_invalid")
        slice_ptr = lib.NewSlice()
        if not slice_ptr:
            raise NativeSdkError("sdk_slice_missing")
        try:
            decrypt_ret = lib.DecryptData(
                str(item.get("random_key") or "").encode("utf-8"),
                str(item.get("encrypt_chat_msg") or "").encode("utf-8"),
                slice_ptr,
            )
            if decrypt_ret != 0:
                raise NativeSdkError("sdk_decrypt_failed", native_code=decrypt_ret)
            payloads.append(_slice_json(lib, slice_ptr))
        finally:
            lib.FreeSlice(slice_ptr)
    return {"ok": True, "payloads": payloads}


def _emit(payload: dict[str, Any]) -> None:
    # This framed stdout stream is an IPC channel captured by the parent, not
    # an operator log.  The native child never writes the frame to a file or
    # logger because decrypted archive content must stay inside the sync path.
    sys.stdout.write(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    operation = args[0] if args else ""
    try:
        request = json.loads(sys.stdin.read() or "{}")
        if not isinstance(request, dict):
            raise NativeSdkError("sdk_request_invalid")
        if operation == "fetch":
            result = _fetch(request)
        elif operation == "decrypt":
            result = _decrypt(request)
        else:
            raise NativeSdkError("sdk_operation_invalid")
    except NativeSdkError as exc:
        _emit({"ok": False, "error_code": exc.error_code, "native_code": exc.native_code})
        return 1
    except Exception as exc:
        _emit({"ok": False, "error_code": f"sdk_helper_{type(exc).__name__}"})
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
