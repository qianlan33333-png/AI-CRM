from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = {
    "aicrm_next/automation_engine/group_ops/broadcast.py": (
        "dispatch_one(",
        "build_upload_command",
    ),
    "aicrm_next/automation_engine/group_ops/action_dispatcher.py": ("INSERT INTO broadcast_jobs",),
    "aicrm_next/background_jobs/broadcast_queue_worker.py": (
        "build_wecom_private_message_adapter",
        "build_wecom_group_message_adapter",
        ".create_private_message_task(",
        ".create_group_message_task(",
    ),
}


def main() -> int:
    violations: list[str] = []
    for relative_path, forbidden_tokens in FORBIDDEN.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in source:
                violations.append(f"{relative_path}: forbidden direct provider owner token: {token}")
    if violations:
        print("\n".join(violations))
        return 1
    print("Group Ops/Broadcast External Effect ownership: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
