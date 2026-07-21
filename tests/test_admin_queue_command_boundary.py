from __future__ import annotations

from pathlib import Path

from scripts.ci.check_admin_queue_command_boundary import ROUTES, collect_errors


def test_admin_queue_command_boundary_accepts_repository_routes() -> None:
    assert collect_errors() == []


def test_admin_queue_command_boundary_rejects_inline_worker_execution(tmp_path: Path) -> None:
    safe_body = """
async def {name}(request):
    return submit_manual_queue_command(request)
"""
    for relative_path, route_names in ROUTES.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(safe_body.format(name=name) for name in route_names),
            encoding="utf-8",
        )
    unsafe_path = tmp_path / "aicrm_next/platform_foundation/external_effects/api.py"
    unsafe_path.write_text(
        """
async def run_external_effect_due(request):
    return worker.run_due(dry_run=False)
""",
        encoding="utf-8",
    )

    errors = collect_errors(tmp_path)

    assert any("inline queue/provider calls are forbidden" in error for error in errors)
    assert any("must submit a QueueRuntimeCommandService command" in error for error in errors)
