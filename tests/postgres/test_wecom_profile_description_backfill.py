from __future__ import annotations

from sqlalchemy.orm import Session

from aicrm_next.platform.shared.db_session import get_engine
from scripts.ops.backfill_wecom_profile_descriptions import _remaining_breakdown


def test_remaining_breakdown_query_is_valid_postgresql(migrated_database_url: str) -> None:
    with Session(get_engine(migrated_database_url)) as session:
        assert _remaining_breakdown(session) == {}
