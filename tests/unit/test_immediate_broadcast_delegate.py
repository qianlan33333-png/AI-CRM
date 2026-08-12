from __future__ import annotations

from typing import Any

from aicrm_next.platform.platform_foundation.background_jobs import immediate_broadcast_delegate


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Executor:
    def __init__(self, job: dict[str, Any], message: dict[str, Any]) -> None:
        self._rows = [job, message]
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, statement: str, params: tuple[Any, ...]) -> _Cursor:
        self.statements.append((statement, params))
        return _Cursor(self._rows.pop(0))


class _Effects:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def plan_effect(self, **payload: Any) -> dict[str, Any]:
        self.calls.append(payload)
        return {"id": 700 + len(self.calls), "created_on_plan": True}


class _BroadcastJobs:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def delegate_external_effect_dbapi(self, _executor: Any, **payload: Any) -> bool:
        self.calls.append(payload)
        return True


class _Projection:
    def __init__(self) -> None:
        self.job_ids: list[int] = []

    def mark_delegated_dbapi(self, _executor: Any, *, job_id: int) -> None:
        self.job_ids.append(job_id)


def _job() -> dict[str, Any]:
    return {
        "id": 88,
        "business_domain": "ai_assistant",
        "status": "queued",
        "batch_key": "batch-88",
        "idempotency_key": "approve-88",
        "trace_id": "trace-88",
        "execution_id": "execution-88",
    }


def _recipient() -> dict[str, Any]:
    return {"id": 12, "unionid": "union-12", "owner_userid": "owner-12"}


def test_immediate_delegate_plans_media_dependency_before_private_message(
    monkeypatch,
) -> None:
    effects = _Effects()
    broadcast_jobs = _BroadcastJobs()
    projection = _Projection()
    executor = _Executor(
        _job(),
        {
            "id": 321,
            "content_text": "带图发送",
            "content_payload_json": {"image_library_ids": [164]},
            "attachments_json": [],
        },
    )

    def _plan_materials(package: dict[str, Any]) -> dict[str, Any]:
        assert package == {"image_library_ids": [164]}
        return {
            "attachments": [
                {"msgtype": "image", "image": {"media_dependency_key": "image:164:image"}}
            ],
            "uploads": [
                {
                    "material_key": "image:164:image",
                    "material_kind": "image",
                    "material_id": 164,
                    "upload_kind": "image",
                }
            ],
        }

    monkeypatch.setattr(
        immediate_broadcast_delegate,
        "_broadcast_material_plan_resolver",
        _plan_materials,
    )
    monkeypatch.setattr(
        immediate_broadcast_delegate,
        "build_broadcast_job_write_port",
        lambda: broadcast_jobs,
    )
    monkeypatch.setattr(
        immediate_broadcast_delegate,
        "build_cloud_broadcast_projection_write_port",
        lambda: projection,
    )

    result = immediate_broadcast_delegate.AiAssistantImmediateBroadcastDelegate(effects).delegate_dbapi(
        executor,
        plan={"plan_id": "plan-1", "content_strategy": "agent_generated_single"},
        recipient=_recipient(),
        job_id=88,
        operator="admin",
        external_userid="external-12",
    )

    assert result["status"] == "created"
    assert result["external_effect_job_id"] == 701
    assert len(effects.calls) == 2
    private, upload = effects.calls
    assert private["effect_type"] == "wecom.message.private.send"
    assert private["status"] == "planned"
    assert private["payload"]["attachments"] == [
        {"msgtype": "image", "image": {"media_dependency_key": "image:164:image"}}
    ]
    assert upload["effect_type"] == "wecom.media.upload"
    assert upload["business_type"] == "broadcast_material_dependency"
    assert upload["status"] == "queued"
    assert upload["payload"]["material_key"] == "image:164:image"
    assert broadcast_jobs.calls[0]["external_effect_job_id"] == 701
    assert projection.job_ids == [88]


def test_immediate_delegate_keeps_direct_attachments_queued_without_media_dependency(
    monkeypatch,
) -> None:
    effects = _Effects()
    broadcast_jobs = _BroadcastJobs()
    projection = _Projection()
    executor = _Executor(
        _job(),
        {
            "id": 322,
            "content_text": "已有企微素材",
            "content_payload_json": {},
            "attachments_json": [{"msgtype": "link", "link": {"title": "资料", "url": "https://example.com"}}],
        },
    )
    monkeypatch.setattr(
        immediate_broadcast_delegate,
        "build_broadcast_job_write_port",
        lambda: broadcast_jobs,
    )
    monkeypatch.setattr(
        immediate_broadcast_delegate,
        "build_cloud_broadcast_projection_write_port",
        lambda: projection,
    )

    result = immediate_broadcast_delegate.AiAssistantImmediateBroadcastDelegate(effects).delegate_dbapi(
        executor,
        plan={"plan_id": "plan-1", "content_strategy": "agent_generated_single"},
        recipient=_recipient(),
        job_id=88,
        operator="admin",
        external_userid="external-12",
    )

    assert result["status"] == "created"
    assert len(effects.calls) == 1
    assert effects.calls[0]["status"] == "queued"
    assert effects.calls[0]["payload"]["attachments"] == [
        {"msgtype": "link", "link": {"title": "资料", "url": "https://example.com"}}
    ]
