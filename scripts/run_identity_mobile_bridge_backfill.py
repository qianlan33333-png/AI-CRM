"""Backfill missing mobile bindings from WeCom identity sources."""
from __future__ import annotations

import argparse
from typing import Any

try:
    from script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.channels.channel_entry.identity_bridge_repo import build_identity_bridge_repository
from aicrm_next.channels.channel_entry.identity_bridge_service import build_identity_bridge_service
from aicrm_next.channel_entry_composition import configure_channel_crm_dependencies
from aicrm_next.shared.runtime import raw_database_url


def _mask_mobile(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    if len(digits) < 7:
        return ""
    return f"{digits[:3]}****{digits[-4:]}"


def _public_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload or {})
    if result.get("mobile"):
        result["mobile_masked"] = _mask_mobile(result.get("mobile"))
        result.pop("mobile", None)
    candidate = result.get("candidate")
    if isinstance(candidate, dict) and candidate.get("mobile"):
        result["candidate"] = {**candidate, "mobile_masked": _mask_mobile(candidate.get("mobile"))}
        result["candidate"].pop("mobile", None)
    binding = result.get("binding")
    if isinstance(binding, dict) and binding.get("mobile"):
        result["binding"] = {**binding, "mobile_masked": _mask_mobile(binding.get("mobile"))}
        result["binding"].pop("mobile", None)
    return result


def _dry_run_external_userid(external_userid: str) -> dict[str, Any]:
    repo = build_identity_bridge_repository()
    binding = repo.get_contact_binding_status(external_userid)
    if binding.get("is_bound"):
        return {
            "external_userid": external_userid,
            "status": "already_bound",
            "binding": binding,
        }
    candidate = repo.get_unique_mobile_candidate_from_identity_sources(external_userid)
    if not candidate:
        return {"external_userid": external_userid, "status": "skipped", "reason": "no_single_candidate"}
    return {
        "external_userid": external_userid,
        "status": "would_bind",
        "candidate": dict(candidate),
    }


def _execute_external_userid(external_userid: str) -> dict[str, Any]:
    repo = build_identity_bridge_repository()
    service = build_identity_bridge_service(repository=repo)
    owner_userid = repo.resolve_binding_owner_userid(external_userid)
    binding = service.bind_mobile_from_identity_sources(
        external_userid,
        owner_userid=owner_userid,
        bind_by_userid=owner_userid or "identity_mobile_bridge_backfill",
    )
    questionnaire_backfill: dict[str, Any] = {"status": "skipped", "reason": "mobile_not_bound"}
    if str((binding or {}).get("mobile") or "").strip() and str((binding or {}).get("status") or "").strip() in {"bound", "already_bound"}:
        questionnaire_backfill = repo.backfill_questionnaire_submissions_for_mobile_binding(
            external_userid=external_userid,
            mobile=str(binding.get("mobile") or "").strip(),
            follow_user_userid=owner_userid,
        )
    return {
        "external_userid": external_userid,
        "status": str((binding or {}).get("status") or "unknown"),
        "binding": binding,
        "questionnaire_backfill": questionnaire_backfill,
    }


def run_backfill(*, execute: bool, limit: int, external_userids: list[str] | None = None) -> dict[str, Any]:
    configure_channel_crm_dependencies()
    repo = build_identity_bridge_repository()
    targets = [value.strip() for value in (external_userids or []) if value.strip()]
    if not targets:
        targets = repo.list_unbound_external_userids_with_identity_sources(limit=limit)
    results: list[dict[str, Any]] = []
    for external_userid in targets[: max(1, int(limit or 500))]:
        result = _execute_external_userid(external_userid) if execute else _dry_run_external_userid(external_userid)
        results.append(_public_result(result))
    summary: dict[str, int] = {}
    for item in results:
        status = str(item.get("status") or "unknown")
        summary[status] = summary.get(status, 0) + 1
    return {
        "ok": True,
        "mode": "execute" if execute else "dry_run",
        "target_count": len(targets),
        "processed_count": len(results),
        "summary": summary,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill missing mobile bindings from identity sources.")
    parser.add_argument("--execute", action="store_true", help="Apply bindings. Default is dry-run only.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum external_userids to scan/process.")
    parser.add_argument("--external-userid", action="append", default=[], help="Process one explicit external_userid. Repeatable.")
    parser.add_argument("--indent", type=int, default=None, help="Pretty-print JSON with this indent.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not raw_database_url():
        print_json(
            {
                "ok": False,
                "error": "DATABASE_URL required",
                "mode": "execute" if args.execute else "dry_run",
                "target_count": 0,
                "processed_count": 0,
                "summary": {},
                "results": [],
            },
            indent=args.indent,
        )
        return 2
    payload = run_backfill(
        execute=bool(args.execute),
        limit=int(args.limit or 500),
        external_userids=list(args.external_userid or []),
    )
    print_json(payload, indent=args.indent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
