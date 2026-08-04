from __future__ import annotations

import json
from pathlib import Path

import pytest

from aicrm_next.extensions.hxc.operation_cycles import local_connector
from aicrm_next.extensions.hxc.operation_cycles.local_connector import (
    CodexAppServerClient,
    ConnectorConfig,
    ConnectorError,
    OperationCycleCodexConnector,
    build_task_prompt,
    load_local_bindings,
)


pytestmark = pytest.mark.unit


def _claim(*, thread_id="", turn_id=""):
    return {
        "ok": True,
        "claimed": True,
        "lease_token": "lease-token-1234567890",
        "request": {
            "request_id": "ocact_safe",
            "run_key": "hxc_monday_20260803",
            "action_key": "prepare_broadcast",
            "action_title": "启动周一群发准备",
            "strategy_version": 2,
            "context_hash": "a" * 64,
            "skill_hash": "b" * 64,
            "thread_id": thread_id,
            "turn_id": turn_id,
        },
        "action": {
            "action_key": "prepare_broadcast",
            "title": "启动周一群发准备",
            "objective": "准备群发",
            "codex_prompt": "人工确认前不得提交。",
        },
        "local_binding_keys": ["excel_workspace", "hxc_knowledge_vault"],
        "context_summary": {"strategy_key": "hxc_monday_full_activation"},
    }


def test_binding_file_exposes_keys_but_prompt_resolves_paths_locally(tmp_path: Path) -> None:
    binding_file = tmp_path / "bindings.json"
    binding_file.write_text(
        json.dumps({"excel_workspace": "/tmp/excel", "hxc_knowledge_vault": "/tmp/vault"}),
        encoding="utf-8",
    )
    bindings = load_local_bindings(binding_file)
    prompt = build_task_prompt(
        _claim(),
        bindings=bindings,
        completion_socket_path=tmp_path / "runner.sock",
    )
    assert "/tmp/excel" in prompt
    assert "submit_operation_cycle_action_result.py" in prompt
    assert "lease-token" not in prompt
    with pytest.raises(ConnectorError, match="disappeared"):
        build_task_prompt(
            _claim(),
            bindings={"excel_workspace": "/tmp/excel"},
            completion_socket_path=tmp_path / "runner.sock",
        )


class _FakeApi:
    def __init__(self) -> None:
        self.events = []

    def post(self, path, payload, *, idempotency_key=""):
        self.events.append((path, payload, idempotency_key))
        return {"ok": True, "status": payload.get("event_type", "ok")}


class _FakeAppServer:
    def __init__(self) -> None:
        self.thread_calls = 0
        self.turn_calls = 0

    def start_thread(self, *, cwd):
        assert cwd == "/tmp/excel"
        self.thread_calls += 1
        return "thread-1"

    def start_turn(self, *, thread_id, prompt):
        assert thread_id == "thread-1"
        assert "CRM 运营动作" in prompt
        self.turn_calls += 1
        return "turn-1"

    def close(self):
        return None


def test_one_action_creates_one_persistent_codex_thread_and_recovers_it(tmp_path: Path) -> None:
    binding_file = tmp_path / "bindings.json"
    binding_file.write_text(
        json.dumps({"excel_workspace": "/tmp/excel", "hxc_knowledge_vault": "/tmp/vault"}),
        encoding="utf-8",
    )
    config = ConnectorConfig(
        crm_base_url="https://crm.example.test",
        runner_id="mac-1",
        codex_socket_path=tmp_path / "codex.sock",
        completion_socket_path=tmp_path / "runner.sock",
        binding_file=binding_file,
        expected_codex_version="codex-cli 1.2.3",
        notify=False,
    )
    api = _FakeApi()
    app_server = _FakeAppServer()
    connector = OperationCycleCodexConnector(config, api=api, app_server=app_server)
    connector.process_claim(_claim())
    connector.process_claim(_claim(thread_id="thread-1", turn_id="turn-1"))
    assert app_server.thread_calls == 1
    assert app_server.turn_calls == 1
    assert [event[1]["event_type"] for event in api.events] == ["thread_bound", "turn_started"]


def test_connector_requires_pinned_codex_version_and_never_mentions_exec(tmp_path: Path) -> None:
    source = Path(
        "aicrm_next/extensions/hxc/operation_cycles/local_connector.py"
    ).read_text(encoding="utf-8")
    assert "expected_codex_version" in source
    assert "codex exec" not in source
    assert '"app-server",\n                    "proxy"' in source
    assert '"--sock"' in source


class _ProxyWriter:
    def __init__(self) -> None:
        self.value = b""

    def write(self, value):
        self.value += value

    def flush(self):
        return None

    def close(self):
        return None


class _ProxyReader:
    def __init__(self) -> None:
        self.lines = [b'{"id":1,"result":{"userAgent":"Codex Desktop/test"}}\n']

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def close(self):
        return None


class _ProxyProcess:
    def __init__(self) -> None:
        self.stdin = _ProxyWriter()
        self.stdout = _ProxyReader()
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_app_server_client_uses_official_unix_socket_proxy(monkeypatch, tmp_path: Path) -> None:
    socket_path = tmp_path / "app-server-control.sock"
    socket_path.touch()
    process = _ProxyProcess()
    command = []

    def fake_popen(argv, **_kwargs):
        command.extend(argv)
        return process

    monkeypatch.setattr(local_connector.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_connector.select, "select", lambda *_args: ([process.stdout], [], []))
    client = CodexAppServerClient(
        socket_path,
        codex_binary=Path("/opt/codex-fixed"),
    )
    client.connect()
    initialize = json.loads(process.stdin.value.decode("utf-8"))
    assert command == [
        "/opt/codex-fixed",
        "app-server",
        "proxy",
        "--sock",
        str(socket_path),
    ]
    assert initialize["method"] == "initialize"
    assert initialize["params"]["capabilities"]["experimentalApi"] is True
    client.close()
