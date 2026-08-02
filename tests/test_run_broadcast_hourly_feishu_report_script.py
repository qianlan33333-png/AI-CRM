from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_broadcast_hourly_feishu_report as runner  # type: ignore[import-not-found]


def test_hourly_feishu_report_script_returns_zero_only_after_delivery(monkeypatch, capsys):
    monkeypatch.setattr(
        runner,
        "run",
        lambda: {"status": "sent", "summary": {"totalJobs": 0, "successJobs": 0, "failedJobs": 0}},
    )

    assert runner.main() == 0
    output = capsys.readouterr().out
    assert '"status": "sent"' in output


@pytest.mark.parametrize(
    "status",
    ["failed", "queued", "skipped_unverified", "unknown"],
)
def test_hourly_feishu_report_script_returns_nonzero_without_delivery(monkeypatch, capsys, status):
    monkeypatch.setattr(
        runner,
        "run",
        lambda: {"status": status, "summary": {"totalJobs": 1, "successJobs": 0, "failedJobs": 1}},
    )

    assert runner.main() == 1
    output = capsys.readouterr().out
    assert f'"status": "{status}"' in output


@pytest.mark.parametrize("status", ["skipped_no_config", "skipped_disabled"])
def test_hourly_feishu_report_script_returns_optional_skip_exit_code(monkeypatch, capsys, status):
    monkeypatch.setattr(
        runner,
        "run",
        lambda: {"status": status, "summary": {"totalJobs": 1, "successJobs": 0, "failedJobs": 0}},
    )

    assert runner.main() == 2
    output = capsys.readouterr().out
    assert f'"status": "{status}"' in output


def test_hourly_feishu_report_script_accepts_same_hour_duplicate(monkeypatch, capsys):
    monkeypatch.setattr(runner, "run", lambda: {"status": "skipped_duplicate", "summary": {}})

    assert runner.main() == 0
    assert '"status": "skipped_duplicate"' in capsys.readouterr().out
