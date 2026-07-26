from __future__ import annotations

from types import SimpleNamespace

import pytest

from aicrm_next.channel_entry import crm_port


def test_channel_crm_port_fails_closed_before_composition(monkeypatch) -> None:
    monkeypatch.setattr(crm_port, "_DEPENDENCIES", None)

    with pytest.raises(RuntimeError, match="channel_crm_port_not_configured"):
        crm_port.build_identity_resolution_queue_port()


def test_channel_crm_port_forwards_structural_identity_request(monkeypatch) -> None:
    calls: list[tuple[object, object, str, bool]] = []
    expected = SimpleNamespace(
        status="resolved",
        identity=SimpleNamespace(unionid="union-1", customer_name="Customer", remark="Remark"),
        reason="",
    )

    def resolve(executor, request, *, placeholder="%s", for_update=False):
        calls.append((executor, request, placeholder, for_update))
        return expected

    dependencies = crm_port.ChannelCrmDependencies(
        resolve_identity_with_dbapi=resolve,
        resolve_external_userid_with_dbapi=lambda *_args, **_kwargs: "union-1",
        identity_resolution_queue_factory=lambda: SimpleNamespace(),
        identity_event_log_factory=lambda **_kwargs: SimpleNamespace(),
        customer_tag_projection_factory=lambda: SimpleNamespace(),
        plan_identity_resolution_effect=lambda *_args, **_kwargs: {"ok": True},
        enqueue_channel_entry_identity_resolution_in_connection=lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(crm_port, "_DEPENDENCIES", dependencies)
    request = crm_port.ResolvePersonIdentityRequest(external_userid="external-1")

    result = crm_port.resolve_identity_with_dbapi("executor", request, placeholder="?", for_update=True)

    assert result is expected
    assert crm_port.resolved_unionid(result) == "union-1"
    assert calls == [("executor", request, "?", True)]
