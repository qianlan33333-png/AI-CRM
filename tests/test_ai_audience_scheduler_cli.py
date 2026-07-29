from __future__ import annotations

import sys

from scripts import run_ai_audience_scheduler


def test_refresh_intent_clock_emits_incremental_and_due_daily_without_inline_consumers(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def emit_due_ticks(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "daily_tick_due": True, "items": []}

    monkeypatch.setattr(run_ai_audience_scheduler, "emit_due_ticks", emit_due_ticks)
    monkeypatch.setattr(run_ai_audience_scheduler, "print_json", lambda payload, **_kwargs: captured.update(payload=payload))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_ai_audience_scheduler.py", "--refresh-intent-clock", "--daily-at", "02:00"],
    )

    assert run_ai_audience_scheduler.main() == 0
    assert captured["include_incremental"] is True
    assert captured["include_daily"] is True
    assert captured["force_daily"] is False
    assert "consumers" not in captured["payload"]
