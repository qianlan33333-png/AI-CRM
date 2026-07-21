from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from aicrm_next.platform_foundation.execution_runtime.cutover import (
    CANONICAL_RUNTIME_SERVICES,
    PR3_LEGACY_PERSISTENT_SERVICES,
    PR3_LEGACY_TIMER_OWNERS,
    REQUIRED_RUNTIME_HEARTBEATS,
    GenerationActivation,
    GenerationState,
    QueueRuntimeCutoverCoordinator,
    QueueRuntimeCutoverRequest,
)
from scripts.ci.check_queue_runtime_cutover_kernel import (
    collect_canary_scope_producer_errors,
    collect_errors,
    collect_queue_policy_assignment_errors,
)
from scripts.ops import cutover_queue_runtime_generation
from scripts.ops import transition_queue_runtime_scope
from scripts.ops import check_ai_audience_refresh_owner


@dataclass
class _FakeRepository:
    events: list[str]
    ready_after: int = 1
    ready_calls: int = 0
    activation_calls: int = 0
    disable_calls: int = 0

    def assert_gate_closed(self, **_kwargs):
        self.events.append("gate_closed")
        return _state(0, False)

    def ready_heartbeat_names(self, *, generation: int):
        self.ready_calls += 1
        self.events.append(f"ready:{generation}:{self.ready_calls}")
        if self.ready_calls < self.ready_after:
            return frozenset()
        return frozenset(REQUIRED_RUNTIME_HEARTBEATS)

    def activate_generation(self, **kwargs):
        self.activation_calls += 1
        self.events.append(f"cas:{kwargs['expected_generation']}:{kwargs['target_generation']}")
        return GenerationActivation(
            before=_state(int(kwargs["expected_generation"]), False),
            after=_state(int(kwargs["target_generation"]), True),
            activated_lanes=tuple(kwargs["lanes"]),
        )

    def disable_claims(self, **kwargs):
        self.disable_calls += 1
        self.events.append(f"disable:{kwargs['expected_generation']}")
        return _state(int(kwargs["expected_generation"]), False)


class _FakeLifecycle:
    def __init__(
        self,
        events: list[str],
        *,
        fail_drain: bool = False,
        fail_verify: bool = False,
        fail_replacement: bool = False,
    ) -> None:
        self.events = events
        self.fail_drain = fail_drain
        self.fail_verify = fail_verify
        self.fail_replacement = fail_replacement

    def stage_target_generation(self, generation: int) -> None:
        self.events.append(f"stage:{generation}")

    def start_target_service(self, service: str) -> None:
        self.events.append(f"start:{service}")

    def stop_legacy_triggers(self, units) -> None:
        self.events.append(f"stop_triggers:{','.join(units)}")

    def stop_legacy_services(self, units) -> None:
        self.events.append(f"stop_services:{','.join(units)}")

    def wait_legacy_services_drained(self, units, timeout_seconds: int) -> None:
        self.events.append(f"drain:{','.join(units)}:{timeout_seconds}")
        if self.fail_drain:
            raise RuntimeError("old owner still active")

    def retire_legacy_units(self, units) -> None:
        self.events.append(f"retire:{','.join(units)}")

    def verify_single_owner(
        self,
        *,
        legacy_triggers,
        legacy_services,
        legacy_persistent_services,
        replacement_active=False,
    ) -> None:
        self.events.append(
            "verify_single_owner:"
            + ",".join((*legacy_triggers, *legacy_services, *legacy_persistent_services))
        )
        if self.fail_verify:
            raise RuntimeError("old owner still enabled")

    def activate_post_cutover_replacements(self, generation: int) -> None:
        self.events.append(f"activate_replacements:{generation}")
        if self.fail_replacement:
            raise RuntimeError("replacement timer failed")

    def deactivate_post_cutover_replacements(self, generation: int) -> None:
        self.events.append(f"deactivate_replacements:{generation}")


def _state(generation: int, claim_enabled: bool) -> GenerationState:
    return GenerationState(
        active_generation=generation,
        claim_enabled=claim_enabled,
        rollout_mode="canary" if claim_enabled else "standby",
        policy_version="queue-v2-test-loopback",
        updated_by="pytest",
        updated_reason="test",
        updated_at=None,
        external_claim_scope="test_loopback",
    )


def _request() -> QueueRuntimeCutoverRequest:
    return QueueRuntimeCutoverRequest(
        expected_generation=0,
        target_generation=17,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("internal_general", "webhook_inbox"),
        actor="pytest",
        reason="cutover ordering test",
        legacy_triggers=("legacy-internal.timer",),
        legacy_services=("legacy-internal.service",),
        legacy_persistent_services=("legacy-inbox.service",),
        readiness_timeout_seconds=5,
        drain_timeout_seconds=20,
    )


def test_cutover_starts_canonical_targets_then_drains_old_owner_before_cas() -> None:
    events: list[str] = []
    repository = _FakeRepository(events, ready_after=2)
    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    result = QueueRuntimeCutoverCoordinator(
        repository=repository,
        lifecycle=_FakeLifecycle(events),
        monotonic=lambda: clock[0],
        sleep=sleep,
    ).activate(_request())

    assert result.after.active_generation == 17
    assert result.after.claim_enabled is True
    assert events[:2] == ["gate_closed", "stage:17"]
    start_events = [f"start:{service}" for service in CANONICAL_RUNTIME_SERVICES]
    assert events[2:5] == start_events
    assert events.index("stop_triggers:legacy-internal.timer") > events.index("ready:17:2")
    assert events.index("stop_services:legacy-inbox.service") > events.index("stop_triggers:legacy-internal.timer")
    assert events.index("drain:legacy-internal.service,legacy-inbox.service:20") < events.index(
        "retire:legacy-internal.timer,legacy-internal.service,legacy-inbox.service"
    )
    assert events.index("ready:17:3") > events.index("drain:legacy-internal.service,legacy-inbox.service:20")
    assert events.index(
        "retire:legacy-internal.timer,legacy-internal.service,legacy-inbox.service"
    ) < events.index("verify_single_owner:legacy-internal.timer,legacy-internal.service,legacy-inbox.service")
    assert events.index(
        "verify_single_owner:legacy-internal.timer,legacy-internal.service,legacy-inbox.service"
    ) < events.index("cas:0:17")
    assert events.index("cas:0:17") < events.index("activate_replacements:17")
    assert events[-1] == "activate_replacements:17"


def test_cutover_drain_failure_keeps_database_claim_gate_closed() -> None:
    events: list[str] = []
    repository = _FakeRepository(events)

    with pytest.raises(RuntimeError, match="old owner still active"):
        QueueRuntimeCutoverCoordinator(
            repository=repository,
            lifecycle=_FakeLifecycle(events, fail_drain=True),
        ).activate(_request())

    assert repository.activation_calls == 0
    assert not any(event.startswith("cas:") for event in events)


def test_cutover_single_owner_verification_failure_keeps_database_claim_gate_closed() -> None:
    events: list[str] = []
    repository = _FakeRepository(events)

    with pytest.raises(RuntimeError, match="old owner still enabled"):
        QueueRuntimeCutoverCoordinator(
            repository=repository,
            lifecycle=_FakeLifecycle(events, fail_verify=True),
        ).activate(_request())

    assert repository.activation_calls == 0
    assert not any(event.startswith("cas:") for event in events)


def test_cutover_replacement_activation_failure_recloses_claim_gate() -> None:
    events: list[str] = []
    repository = _FakeRepository(events)

    with pytest.raises(RuntimeError, match="replacement timer failed"):
        QueueRuntimeCutoverCoordinator(
            repository=repository,
            lifecycle=_FakeLifecycle(events, fail_replacement=True),
        ).activate(_request())

    assert events.index("cas:0:17") < events.index("activate_replacements:17")
    assert events.index("activate_replacements:17") < events.index("disable:17")
    assert events.index("disable:17") < events.index("deactivate_replacements:17")
    assert repository.disable_calls == 1


def test_cutover_rejects_non_monotonic_generation_before_starting_services() -> None:
    events: list[str] = []
    request = _request()
    invalid = QueueRuntimeCutoverRequest(
        **{
            **request.__dict__,
            "expected_generation": 17,
            "target_generation": 17,
        }
    )

    with pytest.raises(ValueError, match="greater than"):
        QueueRuntimeCutoverCoordinator(
            repository=_FakeRepository(events),
            lifecycle=_FakeLifecycle(events),
        ).activate(invalid)

    assert events == []


def test_cutover_cli_is_plan_only_without_explicit_apply(capsys) -> None:
    exit_code = cutover_queue_runtime_generation.main(
        [
            "--expected-generation",
            "0",
            "--target-generation",
            "17",
            "--expected-policy-version",
            "queue-v2-test-loopback",
            "--lane",
            "internal_general",
            "--owner-inventory",
            "pr3",
            "--actor",
            "pytest",
            "--reason",
            "plan only",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"applied": false' in output
    assert '"owner_inventory": "pr3"' in output
    for timer, service in PR3_LEGACY_TIMER_OWNERS:
        assert timer in output
        assert service in output
    for service in PR3_LEGACY_PERSISTENT_SERVICES:
        assert service in output


def test_cutover_cli_rejects_manual_legacy_owner_subset() -> None:
    with pytest.raises(SystemExit):
        cutover_queue_runtime_generation.main(
            [
                "--expected-generation",
                "0",
                "--target-generation",
                "17",
                "--expected-policy-version",
                "queue-v2-test-loopback",
                "--lane",
                "internal_general",
                "--legacy-timer",
                "legacy.timer:legacy.service",
                "--actor",
                "pytest",
                "--reason",
                "manual subset forbidden",
            ]
        )


def test_generation_marker_is_installed_private_and_owned_by_runtime_service(
    monkeypatch,
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []
    marker = tmp_path / "queue-runtime-generation.env"

    def fake_run(command, **_kwargs):
        commands.append(tuple(command))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(cutover_queue_runtime_generation, "_run", fake_run)
    monkeypatch.setattr(cutover_queue_runtime_generation, "RUNTIME_GENERATION_ENV", marker)

    cutover_queue_runtime_generation.SystemdQueueRuntimeLifecycle.write_generation_marker(
        generation=17,
        committed=True,
        test_only=True,
    )

    assert len(commands) == 1
    command = commands[0]
    assert command[:8] == (
        "sudo",
        "install",
        "-o",
        "ubuntu",
        "-g",
        "ubuntu",
        "-m",
        "0600",
    )
    assert command[-1] == str(marker)


def test_scope_transition_cli_is_plan_only_and_lists_fail_closed_sequence(capsys) -> None:
    exit_code = transition_queue_runtime_scope.main(
        [
            "--generation",
            "17",
            "--expected-policy-version",
            "queue-v2-allowlisted",
            "--target-policy-version",
            "queue-v2-test-loopback-rollback",
            "--expected-scope",
            "allowlisted",
            "--target-scope",
            "test_loopback",
            "--actor",
            "pytest",
            "--reason",
            "plan rollback only",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"applied": false' in output
    assert '"disable_claims"' in output
    assert '"drain_all_lanes"' in output
    assert '"resume_claims"' in output
    assert '"real_external_call_executed": false' in output


def test_scope_transition_cli_requires_exact_apply_authorization(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_QUEUE_SCOPE_TRANSITION_AUTHORIZED", raising=False)

    with pytest.raises(RuntimeError, match="AICRM_QUEUE_SCOPE_TRANSITION_AUTHORIZED=1"):
        transition_queue_runtime_scope.main(
            [
                "--generation",
                "17",
                "--expected-policy-version",
                "queue-v2-allowlisted",
                "--target-policy-version",
                "queue-v2-test-loopback-rollback",
                "--expected-scope",
                "allowlisted",
                "--target-scope",
                "test_loopback",
                "--actor",
                "pytest",
                "--reason",
                "explicit rollback",
                "--apply",
                "--confirmation",
                "TRANSITION_QUEUE_SCOPE_17_ALLOWLISTED_TO_TEST_LOOPBACK",
            ]
        )


@pytest.mark.parametrize(
    ("effect_type", "missing_count", "expected_reason"),
    (
        (
            "wecom.external_contact.detail.fetch",
            "external_userid",
            "wecom_external_target_allowlist_empty",
        ),
        (
            "wecom.message.private.send",
            "owner_userid",
            "wecom_owner_allowlist_empty",
        ),
        (
            "wecom.message.group.send",
            "group_chat_id",
            "wecom_group_chat_allowlist_empty",
        ),
        (
            "wecom.media.upload",
            "media_target",
            "wecom_media_target_allowlist_empty",
        ),
    ),
)
def test_allowlisted_scope_preflight_requires_allowlist_for_each_enabled_effect(
    monkeypatch,
    effect_type: str,
    missing_count: str,
    expected_reason: str,
) -> None:
    counts = {
        "external_userid": 1,
        "owner_userid": 1,
        "group_webhook_key": 1,
        "group_chat_id": 1,
        "media_target": 1,
    }
    counts[missing_count] = 0
    monkeypatch.setattr(
        transition_queue_runtime_scope,
        "wecom_canary_policy_snapshot",
        lambda: {
            "provider_target_policy": "allowlisted_canary",
            "allowlist_counts": counts,
            "blocking_reasons": [],
        },
    )
    monkeypatch.setattr(
        transition_queue_runtime_scope,
        "load_wecom_execution_config",
        lambda: SimpleNamespace(
            execution_mode="execute",
            real_calls_enabled=True,
            enabled_effect_types=(effect_type,),
        ),
    )

    result = transition_queue_runtime_scope._policy_preflight("allowlisted")

    assert result["ready"] is False
    assert expected_reason in result["blocking_reasons"]


def test_queue_cutover_ownership_checker_passes() -> None:
    assert collect_errors() == []


def test_queue_cutover_checker_rejects_canary_scope_in_ordinary_producer(tmp_path) -> None:
    producer = tmp_path / "aicrm_next" / "cloud_orchestrator" / "producer.py"
    producer.parent.mkdir(parents=True)
    producer.write_text('payload["execution_scope"] = "allowlisted_canary"\n')

    assert collect_canary_scope_producer_errors(tmp_path) == [
        "aicrm_next/cloud_orchestrator/producer.py: ordinary producers must use the CAS canary authorization service"
    ]


def test_queue_cutover_checker_rejects_default_policy_queue_insert(tmp_path) -> None:
    relative = "aicrm_next/platform_foundation/external_effects/repo.py"
    producer = tmp_path / relative
    producer.parent.mkdir(parents=True)
    producer.write_text(
        "INSERT INTO external_effect_job (status) VALUES ('queued') RETURNING *\n",
        encoding="utf-8",
    )

    errors = collect_queue_policy_assignment_errors(tmp_path)

    assert (
        f"{relative}: external_effect_job insert must bind queue_runtime_control.policy_version"
        in errors
    )


def test_ai_audience_legacy_owner_guard_allows_only_closed_generation_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        check_ai_audience_refresh_owner,
        "_runtime_checks",
        lambda _url: {
            "active_generation": 0,
            "claim_enabled": False,
            "rollout_mode": "standby",
            "legacy_owner_allowed": True,
        },
    )

    state = check_ai_audience_refresh_owner.assert_legacy_owner_allowed(
        "postgresql://localhost/test"
    )

    assert state == {
        "active_generation": 0,
        "claim_enabled": False,
        "rollout_mode": "standby",
        "legacy_owner_allowed": True,
        "real_external_call_executed": False,
    }


def test_ai_audience_legacy_owner_guard_fails_after_generation_activation(monkeypatch) -> None:
    monkeypatch.setattr(
        check_ai_audience_refresh_owner,
        "_runtime_checks",
        lambda _url: {
            "active_generation": 17,
            "claim_enabled": True,
            "rollout_mode": "canary",
            "legacy_owner_allowed": False,
        },
    )

    with pytest.raises(RuntimeError, match="forbidden after queue generation activation"):
        check_ai_audience_refresh_owner.assert_legacy_owner_allowed(
            "postgresql://localhost/test"
        )
