from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()


def _host(value: str) -> str:
    return str(value or "").strip() or "127.0.0.1"


def _port(value: str) -> int:
    try:
        return int(str(value or "").strip() or "5002")
    except ValueError:
        return 5002


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AI-CRM WeCom callback ingress runtime.")
    parser.add_argument("--host", default=os.getenv("WECOM_CALLBACK_INGRESS_HOST") or os.getenv("APP_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=_port(os.getenv("WECOM_CALLBACK_INGRESS_PORT") or "5002"))
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "info"))
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(
        "aicrm_next.channels.channel_entry.ingress_app:app",
        host=_host(args.host),
        port=int(args.port),
        log_level=str(args.log_level or "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
