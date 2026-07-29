from __future__ import annotations

from aicrm_next.external_effect_composition import (
    _resolve_production_wecom_welcome_materials,
    build_external_effect_adapter_registry,
)
from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    ExternalEffectService,
    WECOM_MESSAGE_PRIVATE_SEND,
    WECOM_WELCOME_MESSAGE_SEND,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform.platform_foundation.external_effects.adapters import (
    ExternalEffectAdapterRegistry,
    WeComPrivateMessageAdapter,
    WeComWelcomeMessageAdapter,
    wecom_execution_settings,
)
from aicrm_next.platform.platform_foundation.external_effects.models import ExternalEffectJob
from aicrm_next.platform.platform_foundation.external_effects.repo import build_external_effect_repository
from aicrm_next.platform.platform_foundation.external_effects.worker import ExternalEffectWorker


class _FakeWelcomeAdapter:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def send_welcome_msg(self, payload: dict) -> dict:
        self.payloads.append(dict(payload))
        return {"errcode": 0, "errmsg": "ok"}


class _KnownPrivateProviderFailure:
    def create_private_message_task(self, payload: dict, *, idempotency_key: str = "") -> dict:
        return {
            "ok": False,
            "mode": "production",
            "side_effect_executed": True,
            "result": {"errcode": 40096, "errmsg": "invalid external_userid"},
            "provider_errcode": 40096,
            "provider_error_classification": "terminal",
            "error_code": "wecom_error_40096",
            "error_message": "invalid external_userid",
            "retryable": False,
        }


def _context(trace_id: str = "trace-wecom-welcome") -> CommandContext:
    return CommandContext(
        actor_id="pytest",
        actor_type="system",
        request_id=trace_id,
        trace_id=trace_id,
        source_route="/pytest/wecom-welcome",
    )


def test_private_external_effect_preserves_known_provider_result_without_target_data(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", raising=False)
    adapter = WeComPrivateMessageAdapter(adapter_factory=lambda: _KnownPrivateProviderFailure())
    job = ExternalEffectJob(
        id=7,
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        adapter_name="wecom_private_message",
        operation="send_private_message",
        target_type="external_user",
        target_id="wm_test",
        business_type="broadcast_job",
        business_id="broadcast-7",
        idempotency_key="broadcast-7",
        execution_mode="execute",
        payload_json={
            "channel": "wecom_private",
            "owner_userid": "HuangYouCan",
            "external_userids": ["wm_test"],
            "content_text": "hello",
        },
    )

    result = adapter.dispatch(job)

    assert result.status == "failed_terminal"
    assert result.error_code == "wecom_error_40096"
    assert result.real_external_call_executed is True
    assert result.provider_result_received is True
    assert result.response_summary == {
        "real_external_call_executed": True,
        "wecom_send_executed": True,
        "adapter_mode": "production",
        "exact_target_verified": False,
        "requested_external_userid_count": 1,
        "wecom_msgid_present": False,
        "errcode": 40096,
        "errmsg_present": True,
        "provider_error_classification": "terminal",
        "failed_external_userid_count": 0,
        "provider_result_received": True,
    }
    assert "wm_test" not in str(result.response_summary)


def test_private_external_effect_rejects_unresolved_material_dependency(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", raising=False)
    provider_calls = []

    class Provider:
        def create_private_message_task(self, payload: dict, *, idempotency_key: str = "") -> dict:
            provider_calls.append(payload)
            return {"ok": True, "side_effect_executed": True, "result": {"errcode": 0, "msgid": "unexpected"}}

    job = ExternalEffectJob(
        id=8,
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        adapter_name="wecom_private_message",
        operation="send_private_message",
        target_type="external_contact",
        target_id="wm_test",
        business_type="broadcast_job",
        business_id="8",
        idempotency_key="broadcast-8",
        execution_mode="execute",
        payload_json={
            "channel": "wecom_private",
            "owner_userid": "HuangYouCan",
            "external_userids": ["wm_test"],
            "attachments": [{"msgtype": "image", "image": {"media_dependency_key": "image:12:image"}}],
        },
    )

    result = WeComPrivateMessageAdapter(adapter_factory=lambda: Provider()).dispatch(job)

    assert result.status == "failed_terminal"
    assert result.error_code == "unresolved_material_dependency"
    assert result.real_external_call_executed is False
    assert provider_calls == []


def _plan_welcome_job(
    *,
    repo=None,
    key: str = "welcome-key",
    execution_mode: str = "execute",
    attachments: list[dict] | None = None,
) -> dict:
    service = ExternalEffectService(repo)
    payload = {
        "execution_scope": "allowlisted_canary",
        "welcome_code": "welcome-code",
        "external_userid": "wm_welcome_target",
        "follow_user_userid": "HuangYouCan",
        "text": {"content": "欢迎加入"},
    }
    if attachments:
        payload["attachments"] = attachments
    return service.plan_effect(
        effect_type=WECOM_WELCOME_MESSAGE_SEND,
        adapter_name="wecom_welcome_message",
        operation="send",
        target_type="external_user",
        target_id="wm_welcome_target",
        business_type="channel_entry",
        business_id="channel-1",
        source_module="channel_entry.application",
        source_event_id="evt-1",
        idempotency_key=key,
        payload=payload,
        payload_summary={
            "welcome_code_present": True,
            "external_userid": "wm_welcome_target",
            "follow_user_userid": "HuangYouCan",
            "text_present": True,
        },
        context=_context(),
        status="queued",
        execution_mode=execution_mode,
    )


def _registry(adapter: WeComWelcomeMessageAdapter) -> ExternalEffectAdapterRegistry:
    registry = ExternalEffectAdapterRegistry()
    registry._adapters["wecom_welcome_message"] = adapter  # type: ignore[attr-defined]
    return registry


def test_wecom_welcome_adapter_is_registered_and_advertised() -> None:
    registry = ExternalEffectAdapterRegistry()

    assert registry.get("wecom_welcome_message").__class__.__name__ == "WeComWelcomeMessageAdapter"
    assert WECOM_WELCOME_MESSAGE_SEND in wecom_execution_settings()["supported_types"]


def test_wecom_welcome_disabled_execution_mode_blocks_real_send(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_WELCOME_MESSAGE_SEND)
    monkeypatch.setattr("aicrm_next.platform.platform_foundation.external_effects.worker._capability_gate_error", lambda job: "")
    repo = build_external_effect_repository()
    _plan_welcome_job(repo=repo, execution_mode="disabled")
    fake = _FakeWelcomeAdapter()

    result = ExternalEffectWorker(
        repo,
        adapter_registry=_registry(WeComWelcomeMessageAdapter(adapter_factory=lambda: fake)),
    ).run_due(batch_size=1, dry_run=False, effect_types=[WECOM_WELCOME_MESSAGE_SEND])

    assert result["counts"]["blocked_count"] == 1
    assert result["items"][0]["attempt"]["error_code"] == "shadow_only"
    assert result["real_external_call_executed"] is False
    assert fake.payloads == []


def test_wecom_welcome_missing_composition_never_claims_external_call(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_WELCOME_MESSAGE_SEND)
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS", "wm_welcome_target")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "HuangYouCan")
    monkeypatch.setattr("aicrm_next.platform.platform_foundation.external_effects.worker._capability_gate_error", lambda job: "")
    repo = build_external_effect_repository()
    job = _plan_welcome_job(repo=repo, key="welcome-missing-composition")
    assert ExternalEffectService(repo).authorize_allowlisted_canary(
        job["id"],
        actor="pytest",
        reason="explicit welcome canary authorization",
        expected_version=job["row_version"],
    )

    result = ExternalEffectWorker(
        repo,
        adapter_registry=_registry(WeComWelcomeMessageAdapter()),
    ).dispatch_one(job["id"])

    assert result["job"]["status"] == "failed_terminal"
    assert result["attempt"]["error_code"] == "adapter_composition_missing"
    assert result["attempt"]["response_summary_json"]["wecom_send_executed"] is False
    assert result["attempt"]["response_summary_json"]["real_external_call_executed"] is False
    assert result["real_external_call_executed"] is False


def test_wecom_welcome_executes_through_external_effect_worker(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_WELCOME_MESSAGE_SEND)
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS", "wm_welcome_target")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "HuangYouCan")
    monkeypatch.setattr("aicrm_next.platform.platform_foundation.external_effects.worker._capability_gate_error", lambda job: "")
    repo = build_external_effect_repository()
    job = _plan_welcome_job(repo=repo)
    assert ExternalEffectService(repo).authorize_allowlisted_canary(
        job["id"],
        actor="pytest",
        reason="explicit welcome canary authorization",
        expected_version=job["row_version"],
    )
    fake = _FakeWelcomeAdapter()

    result = ExternalEffectWorker(
        repo,
        adapter_registry=_registry(WeComWelcomeMessageAdapter(adapter_factory=lambda: fake)),
    ).dispatch_one(job["id"])

    assert result["job"]["status"] == "succeeded"
    assert result["attempt"]["status"] == "succeeded"
    assert result["real_external_call_executed"] is True
    assert fake.payloads == [{"welcome_code": "welcome-code", "text": {"content": "欢迎加入"}}]
    assert result["attempt"]["request_summary_json"]["welcome_code_present"] is True
    assert "welcome-code" not in str(result["attempt"]["request_summary_json"])


def test_wecom_welcome_rejects_unresolved_material_without_provider_or_resolver_call(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_WELCOME_MESSAGE_SEND)
    monkeypatch.setattr("aicrm_next.platform.platform_foundation.external_effects.worker._capability_gate_error", lambda job: "")
    repo = build_external_effect_repository()
    job = _plan_welcome_job(
        repo=repo,
        key="welcome-material-resolve",
        attachments=[{"msgtype": "image", "material_id": 110}],
    )
    fake = _FakeWelcomeAdapter()
    resolver_calls: list[list[dict]] = []

    def resolve_materials(attachments: list[dict]) -> list[dict]:
        resolver_calls.append(attachments)
        return [{"msgtype": "image", "image": {"media_id": "resolved-image-media"}}]

    result = ExternalEffectWorker(
        repo,
        adapter_registry=_registry(
            WeComWelcomeMessageAdapter(
                adapter_factory=lambda: fake,
                material_resolver=resolve_materials,
            )
        ),
    ).dispatch_one(job["id"])

    assert result["job"]["status"] == "blocked"
    assert result["attempt"]["error_code"] == "unresolved_material_dependency"
    assert result["real_external_call_executed"] is False
    assert resolver_calls == []
    assert fake.payloads == []


def test_production_welcome_material_translation_uses_wecom_welcome_shapes() -> None:
    class Resolver:
        def resolve_content_package_materials(self, package: dict) -> tuple[list[dict], list[str]]:
            if package.get("image_library_ids"):
                return [], ["image-media"]
            if package.get("attachment_library_ids"):
                return [{"msgtype": "file", "file": {"media_id": "file-media"}}], []
            if package.get("group_invite_library_ids"):
                return [{"msgtype": "link", "link": {"title": "点击入群", "url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef", "desc": "欢迎加入"}}], []
            return [
                {
                    "msgtype": "miniprogram",
                    "miniprogram": {
                        "appid": "wx-app",
                        "page": "pages/index",
                        "title": "欢迎卡片",
                        "pic_media_id": "mini-media",
                    },
                }
            ], []

    resolved = _resolve_production_wecom_welcome_materials(
        [
            {"msgtype": "image", "material_id": 110},
            {"msgtype": "file", "material_id": 120},
            {"msgtype": "miniprogram", "material_id": 130},
            {"msgtype": "link", "material_id": 140},
        ],
        resolver=Resolver(),
    )

    assert resolved == [
        {"msgtype": "image", "image": {"media_id": "image-media"}},
        {"msgtype": "file", "file": {"media_id": "file-media"}},
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx-app",
                "page": "pages/index",
                "title": "欢迎卡片",
                "pic_media_id": "mini-media",
            },
        },
        {
            "msgtype": "link",
            "link": {
                "title": "点击入群",
                "url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
                "desc": "欢迎加入",
            },
        },
    ]


def test_channel_entry_welcome_fallback_private_message_preserves_exact_target(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_MESSAGE_PRIVATE_SEND)
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS", "wm_dynamic_new_contact")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "HuangYouCan")
    calls: list[dict] = []

    class _FakePrivateAdapter:
        def create_private_message_task(self, payload: dict, *, idempotency_key: str = "") -> dict:
            calls.append({"payload": dict(payload), "idempotency_key": idempotency_key})
            return {
                "ok": True,
                "mode": "fake",
                "side_effect_executed": False,
                "exact_target_verified": True,
                "requested_external_userids": list(payload.get("external_userids") or []),
                "wecom_msgid": "fake_msgid",
            }

    monkeypatch.setattr("aicrm_next.platform.platform_foundation.external_effects.worker._capability_gate_error", lambda job: "")
    monkeypatch.setattr("aicrm_next.channels.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: _FakePrivateAdapter())
    repo = build_external_effect_repository()
    job = ExternalEffectService(repo).plan_effect(
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        adapter_name="wecom_private_message",
        operation="send",
        target_type="external_user",
        target_id="wm_dynamic_new_contact",
        business_type="channel_entry_welcome_fallback",
        business_id="channel-1",
        source_module="channel_entry.application",
        source_event_id="evt-1",
        idempotency_key="welcome-fallback-private",
        payload={
            "execution_scope": "allowlisted_canary",
            "channel": "wecom_private",
            "source": "channel_entry_welcome_fallback",
            "owner_userid": "HuangYouCan",
            "external_userids": ["wm_dynamic_new_contact"],
            "content_text": "欢迎加入",
        },
        payload_summary={"text_present": True},
        context=_context("trace-welcome-fallback"),
        status="queued",
        execution_mode="execute",
    )
    assert ExternalEffectService(repo).authorize_allowlisted_canary(
        job["id"],
        actor="pytest",
        reason="explicit fallback canary authorization",
        expected_version=job["row_version"],
    )

    result = ExternalEffectWorker(repo, build_external_effect_adapter_registry()).dispatch_one(job["id"])

    assert result["job"]["status"] == "simulated"
    assert result["attempt"]["status"] == "simulated"
    assert result["attempt"]["request_summary_json"]["business_type"] == "channel_entry_welcome_fallback"
    assert result["attempt"]["request_summary_json"]["source"] == "channel_entry_welcome_fallback"
    assert calls[0]["payload"]["external_userids"] == ["wm_dynamic_new_contact"]
