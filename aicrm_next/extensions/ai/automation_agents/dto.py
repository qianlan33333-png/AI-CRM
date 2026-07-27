from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aicrm_next.send_content.dto import SendContentPackage


class AutomationAgentCreateRequest(BaseModel):
    agent_name: str
    agent_code: str
    bound_package_key: str
    role_prompt: str = ""
    task_prompt: str = ""
    automation_type: str = "agent"
    status: str = "active"
    send_webhook_url: str | None = None
    fixed_content_package: SendContentPackage = Field(default_factory=SendContentPackage)


class AutomationAgentUpdateRequest(BaseModel):
    agent_name: str | None = None
    bound_package_key: str | None = None
    automation_type: str | None = None
    status: str | None = None
    role_prompt: str | None = None
    task_prompt: str | None = None
    send_webhook_url: str | None = None
    fixed_content_package: SendContentPackage | dict[str, Any] | None = None


class FixedContentRequest(BaseModel):
    content_package: SendContentPackage = Field(default_factory=SendContentPackage)
