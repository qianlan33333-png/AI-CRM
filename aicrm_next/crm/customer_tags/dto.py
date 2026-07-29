from __future__ import annotations

from pydantic import BaseModel, Field


class LiveTagRequest(BaseModel):
    external_userid: str = ""
    tag_ids: list[str] = Field(default_factory=list)
    operator: str = ""
    idempotency_key: str = ""
