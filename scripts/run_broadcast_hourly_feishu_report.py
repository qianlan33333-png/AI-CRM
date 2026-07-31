"""系统运营巡检飞书小时报触发脚本。

沿用既有 systemd unit 名称以保证平滑升级；报告覆盖 timer、外部推送和内部消费。
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


def run() -> dict[str, Any]:
    from aicrm_next.platform.admin_jobs.notification_settings import (
        send_broadcast_job_hourly_feishu_report,
    )

    return send_broadcast_job_hourly_feishu_report()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run()
    print_json(result)
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
