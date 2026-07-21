#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.external_effect_composition import build_external_effect_adapter_registry
from aicrm_next.wecom_media_jobs import enqueue_due_media_refreshes
from aicrm_next.media_library.wecom_lease import build_wecom_media_lease_manager
from aicrm_next.media_library.postgres_repo import PostgresMediaLibraryRepository
from aicrm_next.platform_foundation.external_effects import WECOM_MEDIA_UPLOAD
from aicrm_next.platform_foundation.external_effects.repo import build_external_effect_repository
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.shared.runtime import raw_database_url


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill and refresh canonical WeCom temporary media leases.")
    parser.add_argument("--execute", action="store_true", default=False)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-batches", type=int, default=100)
    parser.add_argument("--operator", default="wecom_media_lease_backfill")
    parser.add_argument(
        "--bind-miniprogram",
        action="append",
        default=[],
        metavar="MATERIAL_ID:IMAGE_ID",
        help="Explicitly bind an unresolved legacy miniprogram to a durable image-library source.",
    )
    return parser.parse_args(argv)


def _bindings(values: list[str]) -> dict[int, int]:
    result: dict[int, int] = {}
    for value in values:
        left, separator, right = str(value or "").partition(":")
        if not separator or not left.isdigit() or not right.isdigit():
            raise ValueError(f"invalid --bind-miniprogram value: {value}")
        result[int(left)] = int(right)
    return result


def run(*, execute: bool, batch_size: int, max_batches: int, operator: str, bindings: dict[int, int] | None = None) -> dict:
    bounded_batch = max(1, min(int(batch_size or 10), 50))
    source_repair = PostgresMediaLibraryRepository(raw_database_url()).repair_missing_miniprogram_thumb_sources(
        execute=execute,
        explicit_bindings=bindings,
    )
    manager = build_wecom_media_lease_manager()
    if not execute:
        preview = enqueue_due_media_refreshes(
            dry_run=True,
            limit=bounded_batch,
            manager=manager,
            operator=operator,
            repair_authorized=True,
        )
        return {
            "ok": True,
            "execute": False,
            "source_repair": source_repair,
            "preview": preview,
            "metrics": manager.metrics(),
        }

    repository = build_external_effect_repository()
    worker = ExternalEffectWorker(
        repository,
        build_external_effect_adapter_registry(),
        locked_by="wecom-media-lease-backfill",
        lease_seconds=300,
    )
    batches: list[dict] = []
    totals = {"enqueued_count": 0, "processed_count": 0, "succeeded_count": 0, "failed_count": 0, "blocked_count": 0}
    ok = True
    for _ in range(max(1, int(max_batches or 1))):
        enqueued = enqueue_due_media_refreshes(
            operator=operator,
            limit=bounded_batch,
            manager=manager,
            repository=repository,
            repair_authorized=True,
        )
        if not int(enqueued.get("candidate_count") or 0):
            break
        dispatched = worker.run_due(
            batch_size=bounded_batch,
            dry_run=False,
            effect_types=[WECOM_MEDIA_UPLOAD],
        )
        counts = dict(dispatched.get("counts") or {})
        batch = {"enqueue": enqueued, "dispatch": dispatched}
        batches.append(batch)
        totals["enqueued_count"] += int(enqueued.get("enqueued_count") or 0)
        for key in ("processed_count", "succeeded_count", "failed_count", "blocked_count"):
            totals[key] += int(counts.get(key) or 0)
        if not dispatched.get("ok"):
            ok = False
            break
    remaining = manager.list_due_materials(limit=bounded_batch)
    if remaining:
        ok = False
    return {
        "ok": ok,
        "execute": True,
        "source_repair": source_repair,
        "totals": totals,
        "remaining_candidate_count": len(remaining),
        "remaining_candidates": remaining,
        "metrics": manager.metrics(),
        "batches": batches,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = run(
        execute=bool(args.execute),
        batch_size=int(args.batch_size),
        max_batches=int(args.max_batches),
        operator=str(args.operator or "wecom_media_lease_backfill"),
        bindings=_bindings(list(args.bind_miniprogram or [])),
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
