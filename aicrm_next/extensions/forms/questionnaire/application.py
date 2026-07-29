from __future__ import annotations

import secrets
from typing import Any

from aicrm_next.crm.identity_contact.application import ResolvePersonIdentityQuery
from aicrm_next.crm.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError, blocked_production_payload
from aicrm_next.platform.shared.runtime import production_data_ready
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting, runtime_setting
from aicrm_next.platform.shared.errors import ContractError, NotFoundError

from .domain import admin_detail_projection, extract_submission_mobile, normalize_questionnaire, score_and_tags, summary_projection, validate_required_answers
from .dto import OAuthCallbackRequest, OAuthStartRequest, QuestionnaireSubmitRequest, QuestionnaireUpsertRequest
from .oauth import QuestionnaireOAuthAdapter, build_questionnaire_oauth_adapter
from .public_access import (
    QuestionnaireOAuthConfigReader,
    QuestionnairePublicReadService,
    QuestionnaireSubmissionStatusService,
)
from .repo import QuestionnaireRepository, build_questionnaire_repository
from .side_effect_composition import build_questionnaire_submit_side_effect_gateway


def _read_meta(repo: QuestionnaireRepository) -> dict[str, Any]:
    return {
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "source_status": getattr(repo, "source_status", "local_contract_probe"),
        "read_model_status": getattr(repo, "read_model_status", "fixture"),
        "degraded": False,
        "page_error": "",
    }


def _production_unavailable(exc: Exception) -> dict[str, Any]:
    payload = blocked_production_payload(capability_owner="questionnaire", detail=str(exc))
    payload.update({"route_owner": "ai_crm_next", "fallback_used": False, "read_model_status": "unavailable"})
    return payload


def _repo_or_payload(repo: QuestionnaireRepository | None) -> tuple[QuestionnaireRepository | None, dict[str, Any] | None]:
    try:
        return repo or build_questionnaire_repository(), None
    except RepositoryProviderError as exc:
        return None, _production_unavailable(exc)
    except Exception as exc:
        if production_data_ready():
            return None, _production_unavailable(exc)
        raise


class ListQuestionnairesQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo

    def execute(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        repo, unavailable = _repo_or_payload(self._repo)
        if unavailable:
            return {**unavailable, "items": [], "questionnaires": [], "total": 0, "limit": limit, "offset": offset}
        assert repo is not None
        try:
            rows, total = repo.list_questionnaires(limit=limit, offset=offset)
        except Exception as exc:
            if production_data_ready():
                return {**_production_unavailable(exc), "items": [], "questionnaires": [], "total": 0, "limit": limit, "offset": offset}
            raise
        items = [summary_projection(item) for item in rows]
        return {"ok": True, **_read_meta(repo), "items": items, "questionnaires": items, "total": total, "limit": limit, "offset": offset}

    __call__ = execute


class GetQuestionnaireDetailQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo

    def execute(self, questionnaire_id: int) -> dict[str, Any]:
        repo, unavailable = _repo_or_payload(self._repo)
        if unavailable:
            return unavailable
        assert repo is not None
        try:
            item = repo.get_questionnaire(questionnaire_id)
        except Exception as exc:
            if production_data_ready():
                return _production_unavailable(exc)
            raise
        if not item:
            raise NotFoundError("questionnaire not found")
        return {"ok": True, **_read_meta(repo), **admin_detail_projection(item)}

    __call__ = execute


class GetQuestionnaireEditorQuery(GetQuestionnaireDetailQuery):
    pass


class ListQuestionnaireQuestionsQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo

    def execute(self, questionnaire_id: int) -> dict[str, Any]:
        repo, unavailable = _repo_or_payload(self._repo)
        if unavailable:
            return {**unavailable, "questions": []}
        assert repo is not None
        try:
            questions = repo.list_questions(questionnaire_id)
        except Exception as exc:
            if production_data_ready():
                return {**_production_unavailable(exc), "questions": []}
            raise
        if questions is None:
            raise NotFoundError("questionnaire not found")
        return {"ok": True, **_read_meta(repo), "questionnaire_id": int(questionnaire_id), "questions": questions}

    __call__ = execute


class GetQuestionnaireResultsSummaryQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo

    def execute(self, questionnaire_id: int) -> dict[str, Any]:
        repo, unavailable = _repo_or_payload(self._repo)
        if unavailable:
            return {**unavailable, "results": {}}
        assert repo is not None
        try:
            results = repo.get_results_summary(questionnaire_id)
        except Exception as exc:
            if production_data_ready():
                return {**_production_unavailable(exc), "results": {}}
            raise
        if results is None:
            raise NotFoundError("questionnaire not found")
        return {"ok": True, **_read_meta(repo), "questionnaire_id": int(questionnaire_id), "results": results}

    __call__ = execute


class ListQuestionnaireSubmissionsQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo

    def execute(self, questionnaire_id: int, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        repo, unavailable = _repo_or_payload(self._repo)
        if unavailable:
            return {**unavailable, "items": [], "submissions": [], "total": 0, "limit": limit, "offset": offset}
        assert repo is not None
        try:
            result = repo.list_submissions(questionnaire_id, limit=limit, offset=offset)
        except Exception as exc:
            if production_data_ready():
                return {**_production_unavailable(exc), "items": [], "submissions": [], "total": 0, "limit": limit, "offset": offset}
            raise
        if result is None:
            raise NotFoundError("questionnaire not found")
        rows, total = result
        return {"ok": True, **_read_meta(repo), "questionnaire_id": int(questionnaire_id), "items": rows, "submissions": rows, "total": total, "limit": limit, "offset": offset}

    __call__ = execute


class ListExternalQuestionnaireSubmissionsQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo

    def execute(self, *, filters: dict[str, Any], limit: int = 100, offset: int = 0) -> dict[str, Any]:
        repo, unavailable = _repo_or_payload(self._repo)
        if unavailable:
            return {**unavailable, "items": [], "total": 0, "limit": limit, "offset": offset, "filters": dict(filters or {})}
        assert repo is not None
        try:
            rows, total = repo.list_external_submissions(filters=dict(filters or {}), limit=limit, offset=offset)
        except Exception as exc:
            if production_data_ready():
                return {
                    **_production_unavailable(exc),
                    "items": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "filters": dict(filters or {}),
                }
            raise
        return {
            "ok": True,
            **_read_meta(repo),
            "items": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": dict(filters or {}),
        }

    __call__ = execute


class UpsertQuestionnaireCommand:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo or build_questionnaire_repository()

    def execute(self, payload: QuestionnaireUpsertRequest, questionnaire_id: int | None = None) -> dict[str, Any]:
        if not payload.title.strip():
            raise ContractError("title is required")
        saved = self._repo.save_questionnaire(payload.model_dump(), questionnaire_id)
        if not saved:
            raise NotFoundError("questionnaire not found")
        detail = admin_detail_projection(saved)
        return {"ok": True, **detail}

    __call__ = execute


class SetQuestionnaireEnabledCommand:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo or build_questionnaire_repository()

    def execute(self, questionnaire_id: int, *, enabled: bool) -> dict[str, Any]:
        item = self._repo.set_enabled(questionnaire_id, enabled)
        if not item:
            raise NotFoundError("questionnaire not found")
        return {"ok": True, "questionnaire": summary_projection(item)}

    __call__ = execute


class DeleteQuestionnaireCommand:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo or build_questionnaire_repository()

    def execute(self, questionnaire_id: int) -> dict[str, Any]:
        if not self._repo.get_questionnaire(questionnaire_id):
            raise NotFoundError("questionnaire not found")
        return {"ok": True, "deleted": self._repo.delete_questionnaire(questionnaire_id), "delete_mode": "hard_delete_fixture"}

    __call__ = execute


class ExportQuestionnaireQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo or build_questionnaire_repository()

    def execute(self, questionnaire_id: int) -> dict[str, Any]:
        export = self._repo.export_submissions(questionnaire_id)
        if export is None:
            raise NotFoundError("questionnaire not found")
        return {"ok": True, "export": export}

    __call__ = execute


def _normalized_share_url(value: str) -> str:
    return str(value or "").strip()


def _questionnaire_share_qr_data_url(share_url: str) -> str:
    from aicrm_next.platform.shared.share_qr import jpeg_qr_data_url

    return jpeg_qr_data_url(_normalized_share_url(share_url))


def build_questionnaire_share_payload(questionnaire: dict[str, Any], *, share_url: str) -> dict[str, Any]:
    normalized = summary_projection(questionnaire)
    url = _normalized_share_url(share_url)
    if not url:
        raise ContractError("questionnaire share url is required")
    return {
        "questionnaire_id": normalized["id"],
        "slug": normalized["slug"],
        "title": normalized["title"] or normalized["name"],
        "url": url,
        "public_path": normalized["public_path"],
        "qr_data_url": _questionnaire_share_qr_data_url(url),
    }


class GetQuestionnaireShareQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo or build_questionnaire_repository()

    def execute(self, questionnaire_id: int, *, share_url: str) -> dict[str, Any]:
        item = self._repo.get_questionnaire(questionnaire_id)
        if not item:
            raise NotFoundError("questionnaire not found")
        return {"ok": True, "share": build_questionnaire_share_payload(item, share_url=share_url)}

    __call__ = execute


class GetQuestionnairePreflightQuery:
    def execute(self) -> dict[str, Any]:
        secret_key = runtime_setting("SECRET_KEY")
        wechat_oauth_configured = bool(
            runtime_setting("WECHAT_MP_APP_ID", "").strip()
            and runtime_setting("WECHAT_MP_APP_SECRET")
            and secret_key
            and secret_key != "dev-secret-key-change-me"
        )
        wecom_contact_configured = bool(
            managed_runtime_setting("WECOM_CORP_ID").strip()
            and runtime_setting("WECOM_CONTACT_SECRET")
        )
        wecom_tags_api_available = bool(
            managed_runtime_setting("WECOM_CORP_ID").strip()
            and runtime_setting("WECOM_SECRET")
            and runtime_setting("WECOM_API_BASE", "https://qyapi.weixin.qq.com").strip()
        )
        return {
            "ok": True,
            "checks": {
                "wechat_oauth_configured": wechat_oauth_configured,
                "wecom_contact_configured": wecom_contact_configured,
                "debug_session_api_enabled": True,
                "questionnaire_admin_ui_enabled": True,
                "wecom_tags_api_available": wecom_tags_api_available,
                "identity_map_available": True,
            },
            "status": (
                "ok"
                if wechat_oauth_configured and wecom_contact_configured and wecom_tags_api_available
                else "partial"
            ),
        }

    __call__ = execute


class GetQuestionnaireOAuthConfigQuery:
    def __init__(self, reader: QuestionnaireOAuthConfigReader | None = None) -> None:
        self._reader = reader or QuestionnaireOAuthConfigReader()

    def execute(self) -> dict[str, Any]:
        return self._reader.read()

    __call__ = execute


class LatestSubmitDebugQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo or build_questionnaire_repository()

    def execute(self, questionnaire_id: int) -> dict[str, Any]:
        if not self._repo.get_questionnaire(questionnaire_id):
            raise NotFoundError("questionnaire not found")
        latest = self._repo.latest_submission(questionnaire_id)
        return {"ok": True, "submission": latest, "source_status": "fixture", "safe_debug": True}

    __call__ = execute


class GetPublicQuestionnaireQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._service = QuestionnairePublicReadService(repo)

    def execute(self, slug: str) -> dict[str, Any]:
        return self._service.get_public_questionnaire(slug)

    __call__ = execute


class GetPublicQuestionnaireSubmissionStatusQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._service = QuestionnaireSubmissionStatusService(repo)

    def execute(self, slug: str, *, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._service.get_submission_status(slug, identity=dict(identity or {}))

    __call__ = execute


class SubmitQuestionnaireCommand:
    def __init__(
        self,
        repo: QuestionnaireRepository | None = None,
        identity_query: ResolvePersonIdentityQuery | None = None,
        side_effect_gateway: Any | None = None,
    ) -> None:
        self._repo = repo or build_questionnaire_repository()
        self._identity_query = identity_query or ResolvePersonIdentityQuery()
        self._side_effect_gateway = side_effect_gateway or build_questionnaire_submit_side_effect_gateway()

    def execute(self, slug: str, payload: QuestionnaireSubmitRequest) -> dict[str, Any]:
        item = self._repo.get_questionnaire_by_slug(slug)
        if not item:
            raise NotFoundError("questionnaire not found")
        if not bool(item.get("enabled", True)):
            raise NotFoundError("questionnaire disabled")
        validate_required_answers(item, payload.answers)
        resolved_mobile = extract_submission_mobile(item, payload.answers, payload.respondent_identity)
        identity = self._identity_query(
            ResolvePersonIdentityRequest(
                mobile=resolved_mobile or payload.respondent_identity.get("mobile"),
                external_userid=payload.respondent_identity.get("external_userid"),
                openid=payload.respondent_identity.get("openid"),
                unionid=payload.respondent_identity.get("unionid"),
            )
        )
        score, final_tags = score_and_tags(item, payload.answers)
        submission = self._repo.create_submission(
            {
                "questionnaire_id": item["id"],
                "slug": item["slug"],
                "answers": dict(payload.answers),
                "respondent_identity": dict(payload.respondent_identity),
                "person_id": identity.person_id if identity else None,
                "external_userid": (identity.external_userid if identity else payload.respondent_identity.get("external_userid")) or "",
                "unionid": (identity.unionid if identity else payload.respondent_identity.get("unionid")) or "",
                "mobile": (identity.mobile if identity else resolved_mobile or payload.respondent_identity.get("mobile")) or "",
                "binding_status": identity.binding_status if identity else "unresolved",
                "follow_user_userid": (identity.follow_user_userid if identity else payload.respondent_identity.get("follow_user_userid")) or "",
                "score": score,
                "final_tags": final_tags,
                "result_token": secrets.token_urlsafe(32),
            }
        )
        bind_result = self._side_effect_gateway.bind_mobile(questionnaire=item, submission=submission)
        tag_result = self._side_effect_gateway.apply_tags(
            questionnaire_id=item["id"],
            submission_id=submission["submission_id"],
            external_userid=submission.get("external_userid") or "",
            tag_ids=final_tags,
            unionid=submission.get("unionid") or "",
            follow_user_userid=submission.get("follow_user_userid") or "",
        )
        external_push_config = item.get("external_push_config") if isinstance(item.get("external_push_config"), dict) else {}
        webhook_url = str(external_push_config.get("webhook_url") or "") if external_push_config.get("enabled") else ""
        push_result = self._side_effect_gateway.emit_external_push(
            questionnaire_id=item["id"],
            submission_id=submission["submission_id"],
            webhook_url=webhook_url,
            payload_summary={
                "slug": item["slug"],
                "score": score,
                "final_tag_count": len(final_tags),
                "external_userid": submission.get("external_userid") or "",
            },
        )
        automation_gateway_result = self._side_effect_gateway.emit_automation_questionnaire_result(
            questionnaire=item,
            submission=submission,
            final_tags=final_tags,
        )
        automation_event = self._automation_event_from_gateway(automation_gateway_result)
        questionnaire_projection = normalize_questionnaire(item)
        return {
            "ok": True,
            "submission_id": submission["submission_id"],
            "result_access_token": submission["result_token"],
            "result_url": f"/api/h5/questionnaires/{item['slug']}/result",
            "questionnaire_id": item["id"],
            "slug": item["slug"],
            "external_userid": submission.get("external_userid") or "",
            "person_id": submission.get("person_id"),
            "mobile": submission.get("mobile") or "",
            "binding_status": submission.get("binding_status") or "unresolved",
            "score": score,
            "final_tags": final_tags,
            "redirect_url": item.get("redirect_url") or f"/s/{item['slug']}/submitted",
            "completion_target": questionnaire_projection["completion_target"],
            "completion_target_enabled": questionnaire_projection["completion_target_enabled"],
            "completion_target_type": questionnaire_projection["completion_target_type"],
            "result_message": "提交成功",
            "automation_event": automation_event,
            "side_effect_safety": self._side_effect_gateway.side_effect_safety(),
            "side_effects": {
                "mobile_binding": bind_result,
                "wecom_tag": tag_result,
                "external_push": push_result,
                "automation": automation_gateway_result,
            },
        }

    __call__ = execute

    def _automation_event_from_gateway(self, gateway_result: dict[str, Any]) -> dict[str, Any]:
        result = gateway_result.get("result") if isinstance(gateway_result.get("result"), dict) else {}
        return {
            "ok": True,
            "source_status": result.get("source_status", "fixture_boundary"),
            "member_id": result.get("member_id", ""),
            "followup_type": result.get("followup_type", "normal"),
            "current_pool": result.get("current_pool", ""),
        }


class GetSubmissionResultQuery:
    def __init__(self, repo: QuestionnaireRepository | None = None) -> None:
        self._repo = repo or build_questionnaire_repository()

    def execute(self, slug: str, submission_id: str) -> dict[str, Any]:
        item = self._repo.get_questionnaire_by_slug(slug)
        if not item:
            raise NotFoundError("questionnaire not found")
        submission = self._repo.get_submission(submission_id)
        if not submission or submission.get("slug") != slug:
            raise NotFoundError("submission not found")
        public_result = {key: value for key, value in submission.items() if key != "result_token"}
        return {"ok": True, "result": public_result, "result_message": "提交成功"}

    __call__ = execute


class StartWechatOAuthQuery:
    def __init__(self, adapter: QuestionnaireOAuthAdapter | None = None) -> None:
        self._adapter = adapter or build_questionnaire_oauth_adapter()

    def execute(self, request: OAuthStartRequest) -> dict[str, Any]:
        return self._adapter.build_authorize_url(request)

    __call__ = execute


class CompleteWechatOAuthCallbackCommand:
    def __init__(self, adapter: QuestionnaireOAuthAdapter | None = None) -> None:
        self._adapter = adapter or build_questionnaire_oauth_adapter()

    def execute(self, request: OAuthCallbackRequest) -> dict[str, Any]:
        return self._adapter.callback(request)

    __call__ = execute
