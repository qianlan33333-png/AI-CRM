from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_incremental_archive_sync.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("run_incremental_archive_sync_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def test_run_incremental_archive_sync_uses_internal_http_helper(monkeypatch, capsys):
    module = _load_script_module()
    captured: dict[str, object] = {}

    def fake_urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(b'{"ok": true, "synced_count": 5}')

    monkeypatch.setenv("APP_HOST", "archive.local")
    monkeypatch.setenv("APP_PORT", "5002")
    monkeypatch.setenv("AICRM_INTERNAL_API_BASE_URL", "https://archive.local")
    monkeypatch.setenv("WECOM_DEFAULT_OWNER_USERID", "zhangsan")
    monkeypatch.setattr(module, "read_internal_access_token", lambda **_kwargs: "archive-oauth-access-token")
    monkeypatch.setattr(module, "read_internal_tls_context", lambda: None)
    monkeypatch.setenv("WECOM_ARCHIVE_SYNC_CURSOR", "30651")
    monkeypatch.setenv("WECOM_ARCHIVE_SYNC_LIMIT", "200")
    monkeypatch.setenv("WECOM_ARCHIVE_SYNC_MAX_PAGES", "20")
    monkeypatch.setenv("WECOM_ARCHIVE_SYNC_MODE", "http")
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    body = module.run()

    assert json.loads(body) == {"ok": True, "synced_count": 5}
    assert captured["url"] == "https://archive.local/api/archive/sync"
    assert captured["timeout"] == 600
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["headers"]["authorization"] == "Bearer archive-oauth-access-token"
    assert captured["body"] == {
        "start_time": "2000-01-01 00:00:00",
        "end_time": "2099-12-31 23:59:59",
        "owner_userid": "zhangsan",
        "cursor": "30651",
        "limit": 200,
        "max_pages": 20,
    }
    assert json.loads(capsys.readouterr().out.strip()) == {"ok": True, "synced_count": 5}


def test_run_incremental_archive_sync_defaults_to_direct_process(monkeypatch, capsys):
    module = _load_script_module()
    captured: dict[str, object] = {}
    repository = object()

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "source_status": "next_archive_sync", "reply_monitor_skipped": True}

    monkeypatch.setenv("WECOM_DEFAULT_OWNER_USERID", "HuangYouCan")
    monkeypatch.setenv("WECOM_ARCHIVE_SYNC_CURSOR", "30651")
    monkeypatch.setenv("WECOM_ARCHIVE_SYNC_LIMIT", "100")
    monkeypatch.setenv("WECOM_ARCHIVE_SYNC_MAX_PAGES", "3")
    monkeypatch.delenv("WECOM_ARCHIVE_SYNC_MODE", raising=False)
    monkeypatch.setattr("aicrm_next.admin_jobs_archive_sync_gateway._execute_archive_sync", fake_execute)
    monkeypatch.setattr("aicrm_next.admin_jobs_archive_sync_gateway._build_archive_sync_repository", lambda: repository)

    body = module.run()

    assert json.loads(body)["ok"] is True
    assert captured["owner_userid"] == "HuangYouCan"
    assert captured["cursor"] == "30651"
    assert captured["limit"] == 100
    assert captured["max_pages"] == 3
    assert captured["repo"] is repository
    assert json.loads(capsys.readouterr().out.strip())["reply_monitor_skipped"] is True
