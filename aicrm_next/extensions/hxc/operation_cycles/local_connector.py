from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from aicrm_next.platform.platform_foundation.auth_platform.access_client import (
    AccessTokenLease,
    build_tls_ssl_context,
    fetch_internal_access_token,
)


CONNECTOR_VERSION = "operation-cycle-codex-connector/1.0"


class ConnectorError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ConnectorConfig:
    crm_base_url: str
    runner_id: str
    codex_socket_path: Path
    completion_socket_path: Path
    binding_file: Path
    expected_codex_version: str
    codex_binary: Path = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    heartbeat_seconds: int = 15
    claim_wait_seconds: int = 25
    notify: bool = True

    @classmethod
    def from_mapping(cls, values: dict[str, str]) -> "ConnectorConfig":
        required = {
            "AICRM_BASE_URL": _text(values.get("AICRM_BASE_URL")),
            "AICRM_OPERATION_RUNNER_ID": _text(values.get("AICRM_OPERATION_RUNNER_ID")),
            "AICRM_CODEX_APP_SERVER_SOCKET": _text(values.get("AICRM_CODEX_APP_SERVER_SOCKET")),
            "AICRM_OPERATION_RUNNER_BINDINGS_FILE": _text(
                values.get("AICRM_OPERATION_RUNNER_BINDINGS_FILE")
            ),
            "AICRM_CODEX_EXPECTED_VERSION": _text(values.get("AICRM_CODEX_EXPECTED_VERSION")),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ConnectorError(f"connector configuration missing: {','.join(sorted(missing))}")
        completion = _text(values.get("AICRM_OPERATION_RUNNER_CONTROL_SOCKET"))
        if not completion:
            completion = f"/tmp/aicrm-operation-runner-{required['AICRM_OPERATION_RUNNER_ID']}.sock"
        return cls(
            crm_base_url=required["AICRM_BASE_URL"].rstrip("/"),
            runner_id=required["AICRM_OPERATION_RUNNER_ID"],
            codex_socket_path=Path(required["AICRM_CODEX_APP_SERVER_SOCKET"]),
            completion_socket_path=Path(completion),
            binding_file=Path(required["AICRM_OPERATION_RUNNER_BINDINGS_FILE"]),
            expected_codex_version=required["AICRM_CODEX_EXPECTED_VERSION"],
            codex_binary=Path(
                _text(values.get("AICRM_CODEX_BINARY"))
                or "/Applications/ChatGPT.app/Contents/Resources/codex"
            ),
            notify=_text(values.get("AICRM_OPERATION_RUNNER_NOTIFY") or "true").lower()
            in {"1", "true", "yes", "on"},
        )


def load_local_bindings(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectorError(f"local binding file unavailable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConnectorError("local binding file must be a JSON object")
    bindings = {_text(key): _text(value) for key, value in payload.items() if _text(key) and _text(value)}
    if not bindings:
        raise ConnectorError("local binding file is empty")
    return bindings


class CodexAppServerClient:
    """Newline-delimited JSON-RPC over Codex's official Unix-socket proxy.

    The managed app-server socket is a control transport, not a raw JSON-RPC
    socket. The pinned CLI's ``app-server proxy`` performs that local transport
    handshake and forwards protocol bytes; it does not execute a background
    Codex task and is deliberately not an execution fallback.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        codex_binary: Path = Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        timeout_seconds: int = 30,
    ) -> None:
        self._socket_path = socket_path
        self._codex_binary = codex_binary
        self._timeout_seconds = timeout_seconds
        self._next_id = 1
        self._process: subprocess.Popen[bytes] | None = None
        self._writer = None
        self._reader = None

    def connect(self) -> None:
        self.close()
        if not self._socket_path.exists():
            raise ConnectorError(f"Codex app-server socket unavailable: {self._socket_path}")
        try:
            process = subprocess.Popen(
                [
                    str(self._codex_binary),
                    "app-server",
                    "proxy",
                    "--sock",
                    str(self._socket_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ConnectorError(f"Codex app-server proxy unavailable: {exc}") from exc
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise ConnectorError("Codex app-server proxy has no stdio transport")
        self._process = process
        self._writer = process.stdin
        self._reader = process.stdout
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "aicrm-operation-cycle-runner",
                        "title": "AI-CRM Operation Runner",
                        "version": CONNECTOR_VERSION,
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        reader, writer, process = self._reader, self._writer, self._process
        self._reader = None
        self._writer = None
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if writer is not None:
            writer.close()
        if reader is not None:
            reader.close()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._process is None or self._writer is None or self._reader is None:
            raise ConnectorError("Codex app-server is not connected")
        request_id = self._next_id
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            self._writer.write(_json_bytes(message) + b"\n")
            self._writer.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ConnectorError(f"Codex app-server proxy write failed: {exc}") from exc
        while True:
            ready, _, _ = select.select([self._reader], [], [], self._timeout_seconds)
            if not ready:
                raise ConnectorError(f"Codex app-server {method} timed out")
            line = self._reader.readline()
            if not line:
                code = self._process.poll()
                raise ConnectorError(f"Codex app-server proxy closed (exit={code})")
            try:
                response = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if response.get("id") != request_id:
                continue
            if response.get("error"):
                raise ConnectorError(f"Codex app-server {method} failed: {response['error']}")
            result = response.get("result")
            return dict(result or {}) if isinstance(result, dict) else {"value": result}

    def start_thread(self, *, cwd: str) -> str:
        result = self.request(
            "thread/start",
            {
                "cwd": cwd,
                "approvalPolicy": "on-request",
                "sandbox": "workspace-write",
                "ephemeral": False,
            },
        )
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else result
        thread_id = _text(thread.get("id") if isinstance(thread, dict) else "")
        if not thread_id:
            raise ConnectorError("Codex app-server did not return a thread id")
        return thread_id

    def start_turn(self, *, thread_id: str, prompt: str) -> str:
        result = self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
            },
        )
        turn = result.get("turn") if isinstance(result.get("turn"), dict) else result
        turn_id = _text(turn.get("id") if isinstance(turn, dict) else "")
        if not turn_id:
            raise ConnectorError("Codex app-server did not return a turn id")
        return turn_id


class OperationRunnerApi:
    def __init__(
        self,
        base_url: str,
        *,
        token_fetcher: Callable[..., AccessTokenLease] = fetch_internal_access_token,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_fetcher = token_fetcher
        self._urlopen = urlopen
        self._lease: AccessTokenLease | None = None
        self._lease_deadline = 0.0

    def _token(self) -> str:
        if self._lease is None or time.monotonic() >= self._lease_deadline - 60:
            self._lease = self._token_fetcher(
                purpose="operation_runner",
                audience="external_integration",
                scopes=("read", "write"),
            )
            self._lease_deadline = time.monotonic() + max(0, self._lease.expires_in)
        return self._lease.access_token

    def post(self, path: str, payload: dict[str, Any], *, idempotency_key: str = "") -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=_json_bytes(payload),
            headers=headers,
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=35, context=build_tls_ssl_context()) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise ConnectorError(f"CRM request failed ({exc.code}): {raw[:500]}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ConnectorError(f"CRM request failed: {exc}") from exc
        if not isinstance(body, dict) or body.get("ok") is False:
            raise ConnectorError(f"CRM rejected request: {body}")
        return body


def build_task_prompt(
    claim: dict[str, Any],
    *,
    bindings: dict[str, str],
    completion_socket_path: Path,
) -> str:
    request = dict(claim.get("request") or {})
    action = dict(claim.get("action") or {})
    context = dict(claim.get("context_summary") or {})
    required = [str(key) for key in claim.get("local_binding_keys") or []]
    missing = [key for key in required if key not in bindings]
    if missing:
        raise ConnectorError(f"required local bindings disappeared: {','.join(missing)}")
    local_lines = "\n".join(f"- {key}: {bindings[key]}" for key in required)
    completion_example = {
        "request_id": request.get("request_id"),
        "event_type": "completed",
        "result_file": "/absolute/local/path/to/safe-result.json",
    }
    return (
        f"# CRM 运营动作：{action.get('title') or request.get('action_key')}\n\n"
        f"请求编号：{request.get('request_id')}\n"
        f"运行键：{request.get('run_key')}\n"
        f"策略版本：v{request.get('strategy_version')}\n"
        f"Context SHA-256：{request.get('context_hash')}\n"
        f"Skill SHA-256：{request.get('skill_hash')}\n\n"
        "## 目标与正式 Skill\n\n"
        f"{action.get('objective') or ''}\n\n{action.get('codex_prompt') or ''}\n\n"
        "## 已确认 CRM 上下文\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```\n\n"
        "## 本机绑定（只允许在本机任务中使用，禁止回传 CRM）\n\n"
        f"{local_lines}\n\n"
        "## 完成回传\n\n"
        "完成前必须让人明确确认最终本地文件。把只含聚合结论的 result JSON 写到本机文件后，执行：\n\n"
        f"`python3 tools/submit_operation_cycle_action_result.py --socket {completion_socket_path} "
        f"--request-id {request.get('request_id')} --result-file <result-json-path>`\n\n"
        "禁止在 result 中放个人标识、本地路径、Excel 内容、凭据或原始对话。示意控制消息（result_file 路径不会发送 CRM）：\n\n"
        f"```json\n{json.dumps(completion_example, ensure_ascii=False, indent=2)}\n```\n"
    )


@dataclass
class _ClaimLease:
    lease_token: str
    thread_id: str = ""
    turn_id: str = ""


class OperationCycleCodexConnector:
    def __init__(
        self,
        config: ConnectorConfig,
        *,
        api: OperationRunnerApi | None = None,
        app_server: CodexAppServerClient | None = None,
    ) -> None:
        self.config = config
        self.api = api or OperationRunnerApi(config.crm_base_url)
        self.app_server = app_server or CodexAppServerClient(
            config.codex_socket_path,
            codex_binary=config.codex_binary,
        )
        self.bindings = load_local_bindings(config.binding_file)
        self._claims: dict[str, _ClaimLease] = {}
        self._control_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def compatibility(self) -> tuple[str, str]:
        try:
            actual = subprocess.check_output(
                [str(self.config.codex_binary), "--version"],
                text=True,
                timeout=10,
            ).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return "unavailable", f"unavailable:{type(exc).__name__}"
        if actual != self.config.expected_codex_version:
            return "incompatible", actual
        try:
            self.app_server.connect()
        except ConnectorError:
            return "unavailable", actual
        return "ready", actual

    def heartbeat(self, *, status: str, codex_version: str) -> None:
        self.api.post(
            "/api/operation-cycles/runner/heartbeat",
            {
                "schema_version": "operation_cycle_runner_heartbeat.v1",
                "runner_id": self.config.runner_id,
                "connector_version": CONNECTOR_VERSION,
                "codex_version": codex_version,
                "app_server_protocol": "codex_app_server_jsonrpc_v2",
                "compatibility_status": status,
                "binding_keys": sorted(self.bindings),
                "max_concurrency": 1,
            },
        )

    def claim(self) -> dict[str, Any]:
        return self.api.post(
            "/api/operation-cycles/action-requests/claim",
            {
                "schema_version": "operation_cycle_action_claim.v1",
                "runner_id": self.config.runner_id,
                "wait_seconds": self.config.claim_wait_seconds,
            },
        )

    def _event(
        self,
        request_id: str,
        event_type: str,
        *,
        fields: dict[str, Any],
        event_key: str,
    ) -> dict[str, Any]:
        claim = self._claims.get(request_id)
        if claim is None:
            raise ConnectorError("request lease is not available")
        payload = {
            "schema_version": "operation_cycle_action_event.v1",
            "event_type": event_type,
            "lease_token": claim.lease_token,
            **fields,
        }
        return self.api.post(
            f"/api/operation-cycles/action-requests/{request_id}/events",
            payload,
            idempotency_key=event_key,
        )

    def process_claim(self, claim: dict[str, Any]) -> None:
        if not claim.get("claimed"):
            return
        request = dict(claim.get("request") or {})
        request_id = _text(request.get("request_id"))
        if not request_id:
            raise ConnectorError("claimed request has no request_id")
        lease = _ClaimLease(
            lease_token=_text(claim.get("lease_token")),
            thread_id=_text(request.get("thread_id")),
            turn_id=_text(request.get("turn_id")),
        )
        self._claims[request_id] = lease
        blocked_code = _text((claim.get("context_summary") or {}).get("blocked_code"))
        if blocked_code:
            self._event(
                request_id,
                "failed",
                fields={"failure_code": blocked_code},
                event_key=f"{request_id}:blocked:{blocked_code}",
            )
            return
        if not lease.thread_id:
            workspace = self.bindings.get("excel_workspace") or next(iter(self.bindings.values()))
            lease.thread_id = self.app_server.start_thread(cwd=workspace)
            self._event(
                request_id,
                "thread_bound",
                fields={"thread_id": lease.thread_id},
                event_key=f"{request_id}:thread:{lease.thread_id}",
            )
        if not lease.turn_id:
            prompt = build_task_prompt(
                claim,
                bindings=self.bindings,
                completion_socket_path=self.config.completion_socket_path,
            )
            lease.turn_id = self.app_server.start_turn(thread_id=lease.thread_id, prompt=prompt)
            self._event(
                request_id,
                "turn_started",
                fields={"thread_id": lease.thread_id, "turn_id": lease.turn_id},
                event_key=f"{request_id}:turn:{lease.turn_id}",
            )
            self._notify(_text(request.get("action_title")) or "CRM 运营任务")

    def _notify(self, title: str) -> None:
        if not self.config.notify:
            return
        safe_title = title.replace('"', "'")[:100]
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "已在 Codex 侧边栏创建任务" with title "{safe_title}"',
                ],
                check=False,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return

    def _serve_control(self) -> None:
        path = self.config.completion_socket_path
        if path.exists():
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(4)
        server.settimeout(1)
        try:
            while not self._stop.is_set():
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    raw = connection.recv(256 * 1024)
                    try:
                        message = json.loads(raw.decode("utf-8"))
                        request_id = _text(message.get("request_id"))
                        event_type = _text(message.get("event_type")) or "completed"
                        if event_type == "completed":
                            result = dict(message.get("result") or {})
                            digest = hashlib.sha256(_json_bytes(result)).hexdigest()[:24]
                            response = self._event(
                                request_id,
                                "completed",
                                fields={"result": result},
                                event_key=f"{request_id}:completed:{digest}",
                            )
                        elif event_type == "failed":
                            failure_code = _text(message.get("failure_code"))
                            response = self._event(
                                request_id,
                                "failed",
                                fields={"failure_code": failure_code},
                                event_key=f"{request_id}:failed:{failure_code}",
                            )
                        else:
                            raise ConnectorError("unsupported control event")
                        connection.sendall(_json_bytes(response))
                    except Exception as exc:
                        connection.sendall(_json_bytes({"ok": False, "error": str(exc)[:500]}))
        finally:
            server.close()
            if path.exists():
                path.unlink()

    def run_forever(self) -> None:
        status, codex_version = self.compatibility()
        self._control_thread = threading.Thread(target=self._serve_control, daemon=True)
        self._control_thread.start()
        last_heartbeat = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if status != "ready":
                    status, codex_version = self.compatibility()
                if now - last_heartbeat >= self.config.heartbeat_seconds:
                    try:
                        self.heartbeat(status=status, codex_version=codex_version)
                    except ConnectorError:
                        self._stop.wait(min(self.config.heartbeat_seconds, 5))
                        continue
                    last_heartbeat = now
                if status != "ready":
                    self._stop.wait(min(self.config.heartbeat_seconds, 5))
                    continue
                try:
                    claim = self.claim()
                    self.process_claim(claim)
                except ConnectorError:
                    self.app_server.close()
                    status = "unavailable"
                    self._stop.wait(min(self.config.heartbeat_seconds, 5))
                    continue
                request = dict(claim.get("request") or {})
                if request.get("status") == "turn_started":
                    # The claim also renews the lease. Pace the active-task
                    # renewal so a persistent Codex conversation does not turn
                    # the long-poll endpoint into a hot loop.
                    self._stop.wait(min(self.config.heartbeat_seconds, 15))
        finally:
            self._stop.set()
            self.app_server.close()

    def stop(self) -> None:
        self._stop.set()


__all__ = [
    "CONNECTOR_VERSION",
    "CodexAppServerClient",
    "ConnectorConfig",
    "ConnectorError",
    "OperationCycleCodexConnector",
    "OperationRunnerApi",
    "build_task_prompt",
    "load_local_bindings",
]
