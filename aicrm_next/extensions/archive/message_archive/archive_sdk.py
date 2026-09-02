from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class WeComArchiveError(RuntimeError):
    pass


SDK_HELPER_MODULE = "aicrm_next.extensions.archive.message_archive.sdk_subprocess"
SDK_RESULT_PREFIX = "AICRM_SDK_RESULT="


def _run_sdk_helper(operation: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", SDK_HELPER_MODULE, operation],
            input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WeComArchiveError(f"isolated SDK {operation} timed out") from exc
    except OSError as exc:
        raise WeComArchiveError(f"isolated SDK {operation} could not start") from exc

    stdout = completed.stdout if isinstance(completed.stdout, bytes) else str(completed.stdout).encode("utf-8")
    prefix = SDK_RESULT_PREFIX.encode("ascii")
    framed = [line for line in stdout.splitlines() if line.startswith(prefix)]
    if not framed:
        raise WeComArchiveError(
            f"isolated SDK {operation} exited without a result (exit={completed.returncode})"
        )
    try:
        result = json.loads(framed[-1][len(prefix) :].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WeComArchiveError(f"isolated SDK {operation} returned an invalid result") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        error_code = str((result or {}).get("error_code") or "sdk_helper_failed")
        native_code = int((result or {}).get("native_code") or 0)
        suffix = f":{native_code}" if native_code else ""
        raise WeComArchiveError(f"isolated SDK {operation} failed: {error_code}{suffix}")
    return result


def fetch_chatdata_page(
    lib_path: str,
    corp_id: str,
    archive_secret: str,
    seq: int,
    limit: int,
    timeout: int,
) -> dict[str, Any]:
    result = _run_sdk_helper(
        "fetch",
        {
            "lib_path": str(lib_path or ""),
            "corp_id": str(corp_id or ""),
            "archive_secret": str(archive_secret or ""),
            "seq": int(seq),
            "limit": int(limit),
            "timeout": int(timeout),
        },
        timeout=max(10, int(timeout) + 15),
    )
    payload = result.get("payload")
    if not isinstance(payload, dict):
        raise WeComArchiveError("isolated SDK fetch payload is invalid")
    return dict(payload)


def load_private_key(private_key_path: str):
    key_bytes = Path(private_key_path).read_bytes()
    return serialization.load_pem_private_key(key_bytes, password=None)


def _decrypt_random_key(private_key, encrypted_random_key: str, publickey_ver: int) -> str:
    if int(publickey_ver) != 1:
        raise WeComArchiveError(f"unsupported publickey_ver: {publickey_ver}")

    cipher_bytes = base64.b64decode(encrypted_random_key)
    paddings = [
        padding.PKCS1v15(),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    ]

    last_error = None
    for rsa_padding in paddings:
        try:
            plain = private_key.decrypt(cipher_bytes, rsa_padding)
            return plain.decode("utf-8")
        except Exception as exc:
            last_error = exc

    raise WeComArchiveError(f"failed to decrypt random key: {type(last_error).__name__}")


def decrypt_random_key(private_key_path: str, encrypted_random_key: str, publickey_ver: int) -> str:
    return _decrypt_random_key(load_private_key(private_key_path), encrypted_random_key, publickey_ver)


def decrypt_chat_payloads(
    lib_path: str,
    private_key_path: str,
    encrypted_records: list[dict[str, Any]],
    *,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    if not encrypted_records:
        return []
    private_key = load_private_key(private_key_path)
    items: list[dict[str, str]] = []
    for record in encrypted_records:
        items.append(
            {
                "random_key": _decrypt_random_key(
                    private_key,
                    str(record["encrypt_random_key"]),
                    int(record.get("publickey_ver") or 0),
                ),
                "encrypt_chat_msg": str(record["encrypt_chat_msg"]),
            }
        )
    result = _run_sdk_helper(
        "decrypt",
        {"lib_path": str(lib_path or ""), "items": items},
        timeout=max(10, int(timeout) + len(items)),
    )
    payloads = result.get("payloads")
    if not isinstance(payloads, list) or len(payloads) != len(items) or not all(isinstance(item, dict) for item in payloads):
        raise WeComArchiveError("isolated SDK decrypt payload is invalid")
    return [dict(item) for item in payloads]


def decrypt_chat_payload(
    lib_path: str,
    private_key_path: str,
    encrypted_record: dict[str, Any],
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    return decrypt_chat_payloads(
        lib_path,
        private_key_path,
        [encrypted_record],
        timeout=timeout,
    )[0]


def normalize_timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str) and "-" in value and ":" in value:
        return value
    ts = int(value)
    if ts > 10_000_000_000:
        ts = ts // 1000
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def is_external_userid(value: str | None) -> bool:
    return bool(value) and value.startswith(("wm", "wo"))


def first_non_empty(values: list[str | None]) -> str:
    for value in values:
        if value:
            return value
    return ""


def extract_text_record(
    seq: int,
    encrypted_record: dict[str, Any],
    decrypted_payload: dict[str, Any],
    fallback_owner_userid: str = "",
) -> dict[str, Any] | None:
    record = extract_archive_record(
        seq,
        encrypted_record,
        decrypted_payload,
        fallback_owner_userid=fallback_owner_userid,
    )
    return record if record and record.get("msgtype") == "text" else None


def extract_archive_record(
    seq: int,
    encrypted_record: dict[str, Any],
    decrypted_payload: dict[str, Any],
    fallback_owner_userid: str = "",
) -> dict[str, Any] | None:
    msgtype = str(decrypted_payload.get("msgtype") or "").strip().lower()
    if msgtype not in {"text", "image"}:
        return None

    sender = str(decrypted_payload.get("from") or "")
    tolist = decrypted_payload.get("tolist") or []
    if isinstance(tolist, str):
        tolist = [tolist]
    roomid = str(decrypted_payload.get("roomid") or "")
    chat_type = "group" if roomid else ("private" if len(tolist) == 1 else "group")

    external_candidates = [item for item in [sender, *tolist] if is_external_userid(str(item or ""))]
    internal_candidates = [str(item or "") for item in [sender, *tolist] if item and not is_external_userid(str(item or ""))]
    external_userid = first_non_empty([str(item or "") for item in external_candidates]) or ""
    owner_userid = sender if sender and not is_external_userid(sender) else first_non_empty(internal_candidates)
    owner_userid = owner_userid or fallback_owner_userid
    content = str(((decrypted_payload.get("text") or {}).get("content") or "")).strip()
    if msgtype == "image":
        image_payload = decrypted_payload.get("image")
        if not isinstance(image_payload, dict) or not str(image_payload.get("sdkfileid") or "").strip():
            return None
        content = ""

    if not owner_userid or (msgtype == "text" and not content):
        return None

    combined_payload = {
        "seq": seq,
        "encrypted_record": encrypted_record,
        "decrypted_message": decrypted_payload,
    }

    return {
        "seq": seq,
        "msgid": decrypted_payload.get("msgid") or encrypted_record.get("msgid") or f"seq-{seq}",
        "chat_type": chat_type,
        "external_userid": external_userid,
        "owner_userid": owner_userid,
        "sender": sender,
        "receiver": ",".join(str(item or "") for item in tolist),
        "msgtype": msgtype,
        "content": content,
        "send_time": normalize_timestamp(decrypted_payload.get("msgtime") or decrypted_payload.get("send_time")),
        "raw_payload": json.dumps(combined_payload, ensure_ascii=False),
    }
