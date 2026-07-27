from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from aicrm_next.extensions.archive.message_archive import archive_sdk


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "aicrm_next" / "extensions" / "archive" / "message_archive" / "sdk_subprocess.py"


def test_fetch_runs_vendor_sdk_in_framed_subprocess(monkeypatch) -> None:
    observed: dict = {}

    def fake_run(command, **kwargs):
        observed.update({"command": command, **kwargs})
        framed = archive_sdk.SDK_RESULT_PREFIX + json.dumps(
            {"ok": True, "payload": {"chatdata": [{"seq": 7}]}}
        )
        return subprocess.CompletedProcess(command, 0, stdout=f"vendor diagnostic\n{framed}\n", stderr="")

    monkeypatch.setattr(archive_sdk.subprocess, "run", fake_run)

    result = archive_sdk.fetch_chatdata_page("/safe/sdk.so", "corp", "secret-value", 6, 100, 60)

    request = json.loads(observed["input"])
    assert observed["command"][-2:] == [archive_sdk.SDK_HELPER_MODULE, "fetch"]
    assert observed["capture_output"] is True
    assert request["archive_secret"] == "secret-value"
    assert result == {"chatdata": [{"seq": 7}]}


def test_sdk_subprocess_failure_does_not_surface_secret_or_native_stderr(monkeypatch) -> None:
    secret = "must-not-leak"

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, -6, stdout="", stderr=f"native crash {secret}")

    monkeypatch.setattr(archive_sdk.subprocess, "run", fake_run)

    with pytest.raises(archive_sdk.WeComArchiveError) as exc_info:
        archive_sdk.fetch_chatdata_page("/safe/sdk.so", "corp", secret, 0, 1, 1)

    assert secret not in str(exc_info.value)
    assert "native crash" not in str(exc_info.value)
    assert "exit=-6" in str(exc_info.value)


def test_sdk_helper_imports_only_stdlib_native_boundary_modules() -> None:
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= {"__future__", "ctypes", "json", "sys", "pathlib", "typing"}
    source = HELPER.read_text(encoding="utf-8")
    assert "lib.FreeSlice.restype = None" in source
    assert "lib.DestroySdk.restype = None" in source
