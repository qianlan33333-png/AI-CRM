from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class WeComTagMutationCommand:
    command_id: str = field(default_factory=lambda: uuid4().hex)
    idempotency_key: str = ""
    actor_id: str = "wecom_tag_operator"
    actor_type: str = "user"
    external_userid: str = ""
    tag_ids: list[str] = field(default_factory=list)
    source_route: str = ""
    source_context: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    command_name: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("command_name", None)
        return payload


@dataclass(frozen=True)
class PlanWeComTagMarkCommand(WeComTagMutationCommand):
    command_name: str = "wecom.tag.mark"


@dataclass(frozen=True)
class PlanWeComTagUnmarkCommand(WeComTagMutationCommand):
    command_name: str = "wecom.tag.unmark"


@dataclass(frozen=True)
class PlanCustomerTagAssignmentCommand(WeComTagMutationCommand):
    command_name: str = "wecom.tag.assignment.apply"


@dataclass(frozen=True)
class PlanQuestionnaireTagSideEffectCommand(WeComTagMutationCommand):
    command_name: str = "questionnaire.tag.apply"
