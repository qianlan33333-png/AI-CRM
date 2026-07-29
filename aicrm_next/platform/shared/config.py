from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    app_name: str = "AI-CRM Next"
    environment: str = "experiment"
    user_ops_repo_backend: str = Field(
        default="memory",
        description="User Ops repository backend: memory for default fixture runtime, sqlalchemy for PostgreSQL-ready storage.",
    )
    customer_read_model_repo_backend: str = Field(
        default="memory",
        description="Customer Read Model repository backend: memory for default fixture runtime, sqlalchemy for PostgreSQL-ready storage.",
    )
    database_url: str = Field(
        default="postgresql+psycopg://aicrm_next:aicrm_next@localhost:5432/aicrm_next",
        description="PostgreSQL-first database URL. Fixture repositories remain the default experiment runtime.",
    )


def get_settings() -> Settings:
    return Settings()
