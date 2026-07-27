from __future__ import annotations

from typing import Any

from aicrm_next.shared.runtime import raw_database_url


def connect_h5_wechat_pay_db() -> Any:
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(raw_database_url(), row_factory=dict_row)
