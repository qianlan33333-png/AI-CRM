from __future__ import annotations

from .agent_sqlalchemy_repository import SqlAlchemyAgentRepository


class PostgresAgentRepository(SqlAlchemyAgentRepository):
    """PostgreSQL-backed automation agent repository for Next-native Agent options."""

    source_status = "production_postgres_agent_repository"
