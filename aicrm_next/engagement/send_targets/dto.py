from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class SendTargetRequest:
    target_id: str
    target_id_type: str = "auto"
    sender_userid: str = ""
    strict_owner_match: bool = False
    bypass_dnd: bool = False


@dataclass(frozen=True)
class ResolvedSendTarget:
    ok: bool
    unionid: str
    external_userid: str
    sender_userid: str
    customer_name: str = ""
    owner_userid: str = ""
    target_source: str = "crm_user_identity"
    warnings: list[JsonDict] = field(default_factory=list)
    do_not_disturb_reasons: list[JsonDict] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "unionid": self.unionid,
            "external_userid": self.external_userid,
            "sender_userid": self.sender_userid,
            "customer_name": self.customer_name,
            "owner_userid": self.owner_userid,
            "target_source": self.target_source,
            "warnings": list(self.warnings),
            "do_not_disturb_reasons": list(self.do_not_disturb_reasons),
        }
