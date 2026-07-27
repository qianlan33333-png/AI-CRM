from __future__ import annotations

import inspect

from aicrm_next.extensions.ai.ai_audience_e2e_composition import build_ai_audience_e2e_runner_factory
from aicrm_next.extensions.ai.ai_audience_ops import external_api
from aicrm_next.extensions.ai.ai_audience_ops.e2e_runner import AudienceRealE2ERunner
from aicrm_next.main import create_app
from aicrm_next.ops_enrollment.ai_audience_e2e_gateway import OpsEnrollmentAudienceE2EGateway
from aicrm_next.ops_enrollment.dto import BatchSendRequest


class _RecordingCommand:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.requests: list[BatchSendRequest] = []

    def __call__(self, request: BatchSendRequest) -> dict:
        self.requests.append(request)
        return dict(self.result)


def test_ops_enrollment_gateway_maps_ai_audience_e2e_requests() -> None:
    preview = _RecordingCommand({"ok": True, "eligible_count": 1})
    execute = _RecordingCommand({"ok": True, "sent_count": 1})
    gateway = OpsEnrollmentAudienceE2EGateway(
        preview_command=preview,
        execute_command=execute,
    )

    assert gateway.preview(package_id=17, content="preview") == {"ok": True, "eligible_count": 1}
    assert gateway.execute(package_id=17, content="execute", confirm=False) == {"ok": True, "sent_count": 1}
    assert gateway.execute(package_id=17, content="execute", confirm=True) == {"ok": True, "sent_count": 1}

    preview_request = preview.requests[0]
    assert preview_request.target_source == "ai_audience_package"
    assert preview_request.target_source_id == 17
    assert preview_request.selection_mode == "all_filtered"
    assert preview_request.content == "preview"
    assert preview_request.confirm is False
    assert [request.confirm for request in execute.requests] == [False, True]
    assert all(request.operator == "prod-e2e" for request in execute.requests)


def test_ai_audience_e2e_composition_builds_isolated_factories() -> None:
    first = build_ai_audience_e2e_runner_factory()
    second = build_ai_audience_e2e_runner_factory()

    first_runner = first(repository=object())
    second_runner = second(repository=object())

    assert isinstance(first_runner, AudienceRealE2ERunner)
    assert first is not second
    assert first_runner._user_ops_gateway is not second_runner._user_ops_gateway


def test_ai_audience_e2e_factory_does_not_construct_commands_during_app_startup(monkeypatch) -> None:
    from aicrm_next.extensions.ai import ai_audience_e2e_composition as composition

    calls: list[str] = []

    class Gateway:
        def __init__(self) -> None:
            calls.append("constructed")

    monkeypatch.setattr(composition, "OpsEnrollmentAudienceE2EGateway", Gateway)

    factory = composition.build_ai_audience_e2e_runner_factory()

    assert calls == []
    factory(repository=object())
    assert calls == ["constructed"]


def test_web_apps_own_their_ai_audience_e2e_runner_factory() -> None:
    first_app = create_app()
    second_app = create_app()

    assert first_app.state.ai_audience_e2e_runner_factory is not second_app.state.ai_audience_e2e_runner_factory


def test_external_api_resolves_e2e_runner_from_app_composition() -> None:
    source = inspect.getsource(external_api.external_ai_audience_e2e_run)

    assert "ai_audience_e2e_runner_factory" in source
    assert "AudienceRealE2ERunner(" not in source
