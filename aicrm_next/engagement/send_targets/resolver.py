from __future__ import annotations

from typing import Any, Protocol

from .dto import JsonDict, ResolvedSendTarget, SendTargetRequest


class SendTargetRepository(Protocol):
    def fetch_send_target_by_unionid(self, unionid: str) -> JsonDict | None: ...
    def fetch_send_target_by_external_userid(self, external_userid: str) -> JsonDict | None: ...
    def fetch_do_not_disturb_reasons(self, unionid: str) -> list[JsonDict]: ...


class SendTargetError(Exception):
    def __init__(self, error_code: str, *, status_code: int = 400, message: str = "", details: JsonDict | None = None) -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.status_code = int(status_code)
        self.details = dict(details or {})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_type(value: str) -> str:
    normalized = _text(value).lower()
    if normalized in {"unionid", "external_userid", "auto"}:
        return normalized
    return "auto"


class SendTargetResolver:
    def __init__(self, repo: SendTargetRepository) -> None:
        self.repo = repo

    def resolve(self, request: SendTargetRequest) -> ResolvedSendTarget:
        sender_userid = _text(request.sender_userid)
        if not sender_userid:
            raise SendTargetError("sender_userid_required", status_code=400)
        target_id = _text(request.target_id)
        if not target_id:
            raise SendTargetError("target_identity_not_found", status_code=404, message="target_id is required")

        target_id_type = _valid_type(request.target_id_type)
        row = self._lookup(target_id=target_id, target_id_type=target_id_type)
        if not row:
            raise SendTargetError(
                "target_identity_not_found",
                status_code=404,
                details={"target_id": target_id, "target_id_type": target_id_type},
            )

        unionid = _text(row.get("unionid"))
        external_userid = _text(row.get("primary_external_userid") or row.get("external_userid"))
        if not unionid:
            raise SendTargetError("target_identity_not_found", status_code=404, details={"target_id": target_id})
        if not external_userid:
            raise SendTargetError("target_external_userid_missing", status_code=409, details={"unionid": unionid})

        warnings: list[JsonDict] = []
        owner_userid = _text(row.get("owner_userid") or row.get("primary_owner_userid"))

        dnd_reasons = self.repo.fetch_do_not_disturb_reasons(unionid)
        if dnd_reasons and not request.bypass_dnd:
            raise SendTargetError(
                "do_not_disturb",
                status_code=409,
                details={"unionid": unionid, "do_not_disturb_reasons": dnd_reasons},
            )
        if dnd_reasons:
            warnings.append({"code": "do_not_disturb_bypassed", "do_not_disturb_reasons": dnd_reasons})

        return ResolvedSendTarget(
            ok=True,
            unionid=unionid,
            external_userid=external_userid,
            sender_userid=sender_userid,
            customer_name=_text(row.get("customer_name")),
            owner_userid=owner_userid,
            target_source="crm_user_identity",
            warnings=warnings,
            do_not_disturb_reasons=dnd_reasons,
        )

    def _lookup(self, *, target_id: str, target_id_type: str) -> JsonDict | None:
        if target_id_type == "unionid":
            return self.repo.fetch_send_target_by_unionid(target_id)
        if target_id_type == "external_userid":
            return self.repo.fetch_send_target_by_external_userid(target_id)
        return self.repo.fetch_send_target_by_unionid(target_id) or self.repo.fetch_send_target_by_external_userid(target_id)
