#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.extensions.forms.questionnaire.reconciliation import QuestionnaireRadarReconciliationService


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count R09 questionnaire/effect gaps or repair only durable event continuations.")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--actor", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be >= 1")
    if args.repair and (not str(args.actor).strip() or not str(args.reason).strip()):
        parser.error("--repair requires --actor and --reason")
    return args


def run(*, repair: bool = False, actor: str = "", reason: str = "", limit: int = 100) -> dict:
    service = QuestionnaireRadarReconciliationService()
    if repair:
        return service.repair(actor=actor, reason=reason, limit=limit)
    return service.diagnose()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = run(
        repair=bool(args.repair),
        actor=str(args.actor),
        reason=str(args.reason),
        limit=int(args.limit),
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
