from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json, read_int_env
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json, read_int_env

ensure_repo_root_on_path()

from aicrm_next.extensions.commerce.commerce.fulfillment_reconciliation import CommerceFulfillmentReconciliationService

DEFAULT_BATCH_SIZE = 50


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retired legacy external-push worker compatibility entrypoint (count-only)."
    )
    parser.add_argument("--limit", type=int, default=read_int_env("EXTERNAL_PUSH_WORKER_BATCH_SIZE", DEFAULT_BATCH_SIZE))
    parser.add_argument("--skip-events", action="store_true")
    parser.add_argument("--skip-retries", action="store_true")
    parser.parse_args(argv)

    payload = CommerceFulfillmentReconciliationService().diagnose()
    payload.update(
        {
            "legacy_worker_retired": True,
            "replacement_owner": "payment.succeeded:webhook_order_paid_consumer",
            "database_mutation_performed": False,
            "consumer_executed": False,
            "real_external_call_executed": False,
        }
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
