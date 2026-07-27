from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentListRequest(BaseModel):
    agent_type: str = ""
    status: str = ""
    include_archived: bool = False
    limit: int = 50
    offset: int = 0


class AgentCreateRequest(BaseModel):
    agent_name: str | None = None
    name: str | None = None
    agent_code: str | None = None
    code: str | None = None
    agent_type: str = "metadata"
    status: str = "draft"
    sort_order: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    operator: str = "system"


class AgentOutputListRequest(BaseModel):
    page: int = 1
    page_size: int = 50
    request_id: str = ""
    unionid: str = ""
    userid: str = ""
    agent_code: str = ""
    output_type: str = ""
    applied_status: str = ""
    min_confidence: float | None = None
    max_confidence: float | None = None
    has_error: bool | None = None
    visibility: str = "masked"


class AgentOutputDetailRequest(BaseModel):
    output_id: str
    visibility: str = "masked"


class AgentRunListRequest(BaseModel):
    page: int = 1
    page_size: int = 50
    request_id: str = ""
    run_id: str = ""
    agent_code: str = ""
    run_status: str = ""
    trigger_source: str = ""
    unionid: str = ""
    userid: str = ""
    started_after: str = ""
    started_before: str = ""
    has_error: bool | None = None
    visibility: str = "masked"


class AgentRunDetailRequest(BaseModel):
    run_id: str
    visibility: str = "masked"
