from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Sequence


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "5001"
NEXT_APP_IMPORT = "aicrm_next.main:app"
REMOVED_LEGACY_COMMANDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("run-legacy", "Removed legacy Flask startup command.", ()),
    ("init-db-legacy", "Removed legacy Flask database init command.", ()),
    ("init-db", "Removed legacy Flask database init command.", ()),
    (
        "delete-questionnaire-submissions-legacy",
        "Legacy fallback helper for deleting questionnaire submissions by slug.",
        ("slug",),
    ),
    (
        "delete-questionnaire-submissions",
        "Deprecated alias for delete-questionnaire-submissions-legacy.",
        ("slug",),
    ),
)
REMOVED_LEGACY_COMMAND_NAMES = frozenset(command for command, _, _ in REMOVED_LEGACY_COMMANDS)


def _host() -> str:
    return os.getenv("APP_HOST", DEFAULT_HOST)


def _port() -> int:
    return int(os.getenv("APP_PORT", DEFAULT_PORT))


def _workers() -> int:
    raw = os.getenv("AICRM_UVICORN_WORKERS") or os.getenv("WEB_CONCURRENCY") or "1"
    try:
        workers = int(raw)
    except ValueError:
        workers = 1
    return max(workers, 1)


def run_next() -> None:
    import uvicorn
    from aicrm_next.platform.platform_foundation.error_reporting import install_process_error_reporting

    install_process_error_reporting(component="aicrm_next_web")
    uvicorn.run(NEXT_APP_IMPORT, host=_host(), port=_port(), workers=_workers())



def removed_legacy_command(command: str) -> None:
    raise SystemExit(
        f"{command} has been removed. AI-CRM now starts with Next runtime only. "
        "Use `python app.py run`, `python app.py health`, or `python app.py routes`. "
        "For database schema changes use Alembic migrations."
    )


def print_next_health() -> None:
    from fastapi.testclient import TestClient

    from aicrm_next.main import app

    response = TestClient(app).get("/health")
    print(
        {
            "ok": response.status_code == 200,
            "status_code": response.status_code,
            "default_runtime": "ai_crm_next",
            "route_owner": response.headers.get("X-AICRM-Route-Owner", ""),
        }
    )


def _iter_route_rows(routes: Sequence[object], prefix: str = "") -> Iterator[tuple[str, tuple[str, ...]]]:
    for route in routes:
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            include_context = getattr(route, "include_context", None)
            include_prefix = str(getattr(include_context, "prefix", "") or "")
            yield from _iter_route_rows(getattr(included_router, "routes", ()) or (), prefix=f"{prefix}{include_prefix}")
            continue
        path = getattr(route, "path", "")
        if path:
            methods = tuple(sorted(getattr(route, "methods", []) or ()))
            yield f"{prefix}{path}", methods


def print_next_routes() -> None:
    from aicrm_next.main import app

    try:
        for path, methods in sorted(_iter_route_rows(app.routes), key=lambda item: item[0]):
            print(f"{','.join(methods) or '-'} {path}")
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "AI-CRM runtime entry. Default runtime is AI-CRM Next; "
            "legacy Flask startup commands have been removed."
        )
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="Run AI-CRM Next FastAPI app (default runtime).")
    subparsers.add_parser("health", help="Check AI-CRM Next health via TestClient.")
    subparsers.add_parser("routes", help="Print AI-CRM Next route inventory.")
    cleanup = subparsers.add_parser("legacy-webhook-cleanup", help="Legacy webhook cleanup commands.")
    cleanup_subparsers = cleanup.add_subparsers(dest="legacy_cleanup_command")
    cleanup_mark = cleanup_subparsers.add_parser("mark-deprecated", help="Mark default legacy webhook entries as deprecated.")
    cleanup_mark.add_argument("--operator", default="cli")
    cleanup_run_due = cleanup_subparsers.add_parser("run-due", help="Run due legacy webhook cleanup candidates.")
    cleanup_run_due.add_argument("--dry-run", action="store_true", default=False, help="Preview cleanup without mutating state.")
    cleanup_run_due.add_argument("--execute", action="store_true", default=False, help="Execute due cleanup entries.")
    cleanup_run_due.add_argument("--limit", type=int, default=50)
    cleanup_run_due.add_argument("--operator", default="cli")
    cleanup_retire_now = cleanup_subparsers.add_parser("retire-now", help="Immediately retire scheduled legacy webhook entries after safety checks.")
    cleanup_retire_now.add_argument("--dry-run", action="store_true", default=False, help="Preview immediate retirement without mutating state.")
    cleanup_retire_now.add_argument("--execute", action="store_true", default=False, help="Retire scheduled legacy entries now.")
    cleanup_retire_now.add_argument("--limit", type=int, default=50)
    cleanup_retire_now.add_argument("--operator", default="cli")
    external_effects = subparsers.add_parser("external-effects", help="External Effect Queue commands.")
    external_effects_subparsers = external_effects.add_subparsers(dest="external_effects_command")
    external_effects_run_due = external_effects_subparsers.add_parser("run-due", help="Run due External Effect Queue jobs.")
    external_effects_run_due.add_argument("--dry-run", action="store_true", default=False, help="Preview due jobs without executing adapters.")
    external_effects_run_due.add_argument("--execute", action="store_true", default=False, help="Execute due jobs when the scheduler switch is enabled.")
    external_effects_run_due.add_argument("--limit", type=int, default=0)
    external_effects_run_due.add_argument("--operator", default="cli")
    external_effects_complete = external_effects_subparsers.add_parser("complete-record-only", help="Complete historical shadow/plan-only External Effect records without sending.")
    external_effects_complete.add_argument("--dry-run", action="store_true", default=False, help="Preview record-only jobs without mutating state.")
    external_effects_complete.add_argument("--execute", action="store_true", default=False, help="Mark historical record-only jobs as succeeded with a synthetic attempt.")
    external_effects_complete.add_argument("--limit", type=int, default=100)
    external_effects_complete.add_argument("--operator", default="cli")
    targets = subparsers.add_parser("p0-1-test-targets", help="P0-1 production test target manifest commands.")
    targets_subparsers = targets.add_subparsers(dest="p0_1_targets_command")
    targets_validate = targets_subparsers.add_parser("validate", help="Validate the P0-1 production test target manifest.")
    targets_validate.add_argument("manifest", nargs="?", default="docs/queue/p0-1-production-test-targets.yaml")
    for removed_command, help_text, positional_args in REMOVED_LEGACY_COMMANDS:
        removed_parser = subparsers.add_parser(removed_command, help=help_text)
        for arg in positional_args:
            removed_parser.add_argument(arg)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    if command == "run":
        run_next()
        return
    if command == "health":
        print_next_health()
        return
    if command == "routes":
        print_next_routes()
        return
    if command == "legacy-webhook-cleanup":
        if getattr(args, "legacy_cleanup_command", "") == "mark-deprecated":
            from aicrm_next.platform.platform_foundation.legacy_cleanup.jobs import print_mark_deprecated_result

            print_mark_deprecated_result(operator=getattr(args, "operator", "cli"))
            return
        if getattr(args, "legacy_cleanup_command", "") == "run-due":
            from aicrm_next.platform.platform_foundation.legacy_cleanup.jobs import print_run_due_result

            dry_run = not bool(getattr(args, "execute", False))
            if getattr(args, "dry_run", False):
                dry_run = True
            print_run_due_result(dry_run=dry_run, limit=getattr(args, "limit", 50), operator=getattr(args, "operator", "cli"))
            return
        if getattr(args, "legacy_cleanup_command", "") == "retire-now":
            from aicrm_next.platform.platform_foundation.legacy_cleanup.jobs import print_retire_now_result

            dry_run = not bool(getattr(args, "execute", False))
            if getattr(args, "dry_run", False):
                dry_run = True
            print_retire_now_result(dry_run=dry_run, limit=getattr(args, "limit", 50), operator=getattr(args, "operator", "cli"))
            return
        parser.print_help()
        return
    if command == "external-effects":
        if getattr(args, "external_effects_command", "") == "run-due":
            from aicrm_next.platform.platform_foundation.external_effects.jobs import print_run_due_result

            dry_run = not bool(getattr(args, "execute", False))
            if getattr(args, "dry_run", False):
                dry_run = True
            limit = int(getattr(args, "limit", 0) or 0) or None
            print_run_due_result(dry_run=dry_run, limit=limit, operator=getattr(args, "operator", "cli"))
            return
        if getattr(args, "external_effects_command", "") == "complete-record-only":
            from aicrm_next.platform.platform_foundation.external_effects.jobs import print_complete_record_only_result

            dry_run = not bool(getattr(args, "execute", False))
            if getattr(args, "dry_run", False):
                dry_run = True
            print_complete_record_only_result(
                dry_run=dry_run,
                limit=int(getattr(args, "limit", 100) or 100),
                operator=getattr(args, "operator", "cli"),
            )
            return
        parser.print_help()
        return
    if command == "p0-1-test-targets":
        if getattr(args, "p0_1_targets_command", "") == "validate":
            from scripts.p0_1_validate_test_targets import main as validate_targets_main

            raise SystemExit(validate_targets_main([getattr(args, "manifest", "docs/queue/p0-1-production-test-targets.yaml")]))
        parser.print_help()
        return
    if command in REMOVED_LEGACY_COMMAND_NAMES:
        removed_legacy_command(command)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
