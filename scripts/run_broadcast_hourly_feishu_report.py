"""群发队列飞书小时报触发脚本。

定时任务通过 systemd/cron 拉起本脚本；脚本只调用服务端小时报逻辑，不在前端做轮询。
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
