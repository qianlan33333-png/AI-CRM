from __future__ import annotations

from .repo_support import (
    Any,
    Callable,
    RepositoryProviderError,
    _answer_snapshots,
    _answers_from_snapshots,
    _external_push_log_threads,
    _external_submission_projection,
    _identity_lookup_values,
    _initial_questionnaires,
    _matches_external_submission_filters,
    _normalized_external_push_log,
    _now,
    _text,
    deepcopy,
    normalize_completion_target_for_storage,
)

class InMemoryQuestionnaireRepository:
    source_status = "local_contract_probe"
    read_model_status = "fixture"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._questionnaires = _initial_questionnaires()
        self._submissions: list[dict[str, Any]] = [
            {
                "submission_id": "sub_fixture_001",
                "result_token": "result_fixture_001_grant_7e3a9c5b2d8f4a61",
                "questionnaire_id": 1,
                "slug": "hxc-activation-v1",
                "answers": {"q_activation": "activated"},
                "respondent_identity": {"mobile": "mobile_masked_fixture"},
                "person_id": "person_fixture",
                "external_userid": "external_user_masked_fixture",
                "mobile": "mobile_masked_fixture",
                "score": 10,
                "final_tags": ["tag_hxc_activated"],
                "created_at": "2026-05-20T10:10:00Z",
            }
        ]
        self._external_push_logs: list[dict[str, Any]] = []
        self._next_id = max(item["id"] for item in self._questionnaires) + 1
        self._next_submission = len(self._submissions) + 1
        self._next_external_push_log = 1

    def list_questionnaires(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = deepcopy(self._questionnaires)
        return rows[offset : offset + limit], len(rows)

    def _raw_questionnaire(self, questionnaire_id: int) -> dict[str, Any] | None:
        for item in self._questionnaires:
            if int(item["id"]) == int(questionnaire_id):
                return item
        return None

    def get_questionnaire(self, questionnaire_id: int) -> dict[str, Any] | None:
        item = self._raw_questionnaire(questionnaire_id)
        if item is None:
            return None
        payload = deepcopy(item)
        payload["submissions_summary"] = self.get_results_summary(questionnaire_id) or {}
        payload["submissions"] = (self.list_submissions(questionnaire_id, limit=10, offset=0) or ([], 0))[0]
        return payload

    def get_questionnaire_by_slug(self, slug: str) -> dict[str, Any] | None:
        slug = str(slug or "").strip()
        for item in self._questionnaires:
            if item.get("slug") == slug:
                return deepcopy(item)
        return None

    def list_questions(self, questionnaire_id: int) -> list[dict[str, Any]] | None:
        item = self._raw_questionnaire(questionnaire_id)
        if not item:
            return None
        return deepcopy(item.get("questions") or [])

    def get_results_summary(self, questionnaire_id: int) -> dict[str, Any] | None:
        item = self._raw_questionnaire(questionnaire_id)
        if not item:
            return None
        rows = [submission for submission in self._submissions if int(submission.get("questionnaire_id") or 0) == int(questionnaire_id)]
        return {
            "questionnaire_id": int(questionnaire_id),
            "submission_count": len(rows),
            "latest_submitted_at": rows[-1].get("created_at") if rows else "",
            "average_score": sum(float(row.get("score") or 0) for row in rows) / len(rows) if rows else 0,
            "result_config": deepcopy(item.get("result_config") or {}),
            "rules": deepcopy(item.get("rules") or []),
        }

    def list_submissions(self, questionnaire_id: int, *, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int] | None:
        questionnaire = self._raw_questionnaire(questionnaire_id)
        if not questionnaire:
            return None
        rows = []
        for item in self._submissions:
            if int(item.get("questionnaire_id") or 0) != int(questionnaire_id):
                continue
            row = deepcopy(item)
            row.setdefault("unionid", _text((row.get("respondent_identity") or {}).get("unionid")))
            if "answer_snapshots" not in row:
                row["answer_snapshots"] = _answer_snapshots(questionnaire.get("questions") or [], dict(row.get("answers") or {}))
            rows.append(row)
        return rows[int(offset) : int(offset) + int(limit)], len(rows)

    def list_external_submissions(
        self,
        *,
        filters: dict[str, Any],
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        questionnaire_by_id = {int(item["id"]): item for item in self._questionnaires}
        rows: list[dict[str, Any]] = []
        for item in self._submissions:
            questionnaire = questionnaire_by_id.get(int(item.get("questionnaire_id") or 0))
            if not questionnaire:
                continue
            row = deepcopy(item)
            row.setdefault("submitted_at", row.get("created_at"))
            row.setdefault("mobile", row.get("mobile_snapshot") or row.get("mobile"))
            row.setdefault(
                "assessment_result_snapshot", (row.get("result_json") or {}).get("assessment_result") if isinstance(row.get("result_json"), dict) else {}
            )
            row["questionnaire_title"] = _text(questionnaire.get("title") or questionnaire.get("name"))
            if "answer_snapshots" not in row:
                row["answer_snapshots"] = _answer_snapshots(questionnaire.get("questions") or [], dict(row.get("answers") or {}))
            if _matches_external_submission_filters(row, filters):
                rows.append(row)
        rows.sort(key=lambda item: (_text(item.get("submitted_at") or item.get("created_at")), _text(item.get("submission_id"))), reverse=True)
        page = rows[int(offset) : int(offset) + int(limit)]
        return [_external_submission_projection(row, row.get("answer_snapshots") or []) for row in page], len(rows)

    def save_questionnaire(self, payload: dict[str, Any], questionnaire_id: int | None = None) -> dict[str, Any]:
        now = _now()
        if questionnaire_id is None:
            item = {
                "id": self._next_id,
                "slug": str(payload.get("slug") or f"questionnaire-{self._next_id}").strip(),
                "created_at": now,
                "submission_count": 0,
                "assessment_enabled": False,
                "lead_channel_id": None,
            }
            self._next_id += 1
            self._questionnaires.append(item)
        else:
            item = next((entry for entry in self._questionnaires if int(entry["id"]) == int(questionnaire_id)), None)
            if item is None:
                return {}
        item.update(
            {
                "title": str(payload.get("title") or item.get("title") or "").strip(),
                "name": str(payload.get("title") or item.get("name") or "").strip(),
                "description": str(payload.get("description") or ""),
                "enabled": bool(payload.get("enabled", item.get("enabled", True))),
                "redirect_url": str(payload.get("redirect_url") or ""),
                "completion_target_json": deepcopy(
                    payload.get("completion_target_json") or normalize_completion_target_for_storage(payload, legacy_url_key="redirect_url")
                ),
                "submit_button_text": str(payload.get("submit_button_text") or "提交"),
                "answer_display_mode": str(payload.get("answer_display_mode") or item.get("answer_display_mode") or "all_in_one"),
                "assessment_enabled": bool(payload.get("assessment_enabled", item.get("assessment_enabled", False))),
                "assessment_config": deepcopy(payload.get("assessment_config") or payload.get("result_config") or item.get("assessment_config") or {}),
                "result_config": deepcopy(payload.get("result_config") or payload.get("assessment_config") or item.get("result_config") or {}),
                "updated_at": now,
                "questions": deepcopy(payload.get("questions") or item.get("questions") or []),
                "score_rules": deepcopy(payload.get("score_rules") or payload.get("rules") or item.get("score_rules") or []),
                "rules": deepcopy(payload.get("rules") or payload.get("score_rules") or item.get("rules") or []),
                "external_push_config": deepcopy(payload.get("external_push_config") or item.get("external_push_config") or {}),
            }
        )
        return deepcopy(item)

    def save_completion_operations(
        self,
        questionnaire_id: int,
        *,
        lead_channel_id: int | None,
        completion_target_json: dict[str, Any],
        redirect_url: str,
    ) -> dict[str, Any] | None:
        item = self._raw_questionnaire(questionnaire_id)
        if item is None:
            return None
        item["lead_channel_id"] = int(lead_channel_id or 0) or None
        item["completion_target_json"] = deepcopy(completion_target_json)
        item["redirect_url"] = str(redirect_url or "").strip()
        item["updated_at"] = _now()
        return self.get_questionnaire(questionnaire_id)

    def save_external_push_operations(
        self,
        questionnaire_id: int,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        item = self._raw_questionnaire(questionnaire_id)
        if item is None:
            return None
        normalized = deepcopy(config)
        item["external_push_config"] = normalized
        item["external_push_enabled"] = bool(normalized.get("enabled"))
        item["external_push_url"] = str(normalized.get("webhook_url") or "").strip()
        item["external_push_type"] = str(normalized.get("type") or "").strip()
        item["external_push_expires_at_ts"] = normalized.get("expires_at_ts")
        item["external_push_day"] = normalized.get("day")
        item["external_push_frequency"] = normalized.get("frequency")
        item["external_push_remark"] = str(normalized.get("remark") or "").strip()
        item["external_push_custom_params"] = deepcopy(normalized.get("custom_params") or [])
        item["updated_at"] = _now()
        return self.get_questionnaire(questionnaire_id)

    def set_enabled(self, questionnaire_id: int, enabled: bool) -> dict[str, Any] | None:
        item = next((entry for entry in self._questionnaires if int(entry["id"]) == int(questionnaire_id)), None)
        if item is None:
            return None
        item["enabled"] = bool(enabled)
        item["updated_at"] = _now()
        return deepcopy(item)

    def delete_questionnaire(self, questionnaire_id: int) -> bool:
        before = len(self._questionnaires)
        self._questionnaires = [item for item in self._questionnaires if int(item["id"]) != int(questionnaire_id)]
        return len(self._questionnaires) < before

    def create_submission(
        self,
        payload: dict[str, Any],
        *,
        internal_event_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        submission = deepcopy(payload)
        submission["submission_id"] = submission.get("submission_id") or f"sub_next_{self._next_submission:03d}"
        submission["created_at"] = submission.get("created_at") or _now()
        questionnaire = self._raw_questionnaire(int(submission["questionnaire_id"]))
        if questionnaire and "answer_snapshots" not in submission:
            submission["answer_snapshots"] = _answer_snapshots(questionnaire.get("questions") or [], dict(submission.get("answers") or {}))
        self._next_submission += 1
        self._submissions.append(submission)
        for item in self._questionnaires:
            if int(item["id"]) == int(submission["questionnaire_id"]):
                item["submission_count"] = int(item.get("submission_count") or 0) + 1
                item["updated_at"] = _now()
        if internal_event_factory is not None:
            request = internal_event_factory(deepcopy(submission))
            if request is None:
                raise RepositoryProviderError("questionnaire.submitted event identity is incomplete")
            from aicrm_next.platform_foundation.internal_events.service import InternalEventService

            emitted = InternalEventService().emit_event(
                event_type=request.event_type,
                aggregate_type=request.aggregate_type,
                aggregate_id=request.aggregate_id,
                payload=request.payload,
                payload_summary=request.payload_summary,
                context=request.context,
                event_version=request.event_version,
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                idempotency_key=request.idempotency_key,
                source_module=request.source_module,
                source_command_id=request.source_command_id,
                correlation_id=request.correlation_id,
                occurred_at=request.occurred_at,
                tenant_id=request.tenant_id,
            )
            submission["internal_event"] = dict(emitted.get("event") or {})
            submission["internal_event_consumer_runs"] = list(emitted.get("consumer_runs") or [])
            self._submissions[-1] = deepcopy(submission)
        return deepcopy(submission)

    def get_submission(self, submission_id: str) -> dict[str, Any] | None:
        for item in self._submissions:
            if item.get("result_token") == submission_id:
                payload = deepcopy(item)
                if isinstance(payload.get("answer_snapshots"), list):
                    payload["answers"] = _answers_from_snapshots(payload["answer_snapshots"])
                    payload["answers_json"] = payload["answers"]
                return payload
        return None

    def get_submission_by_record_id(self, submission_id: str) -> dict[str, Any] | None:
        normalized_id = _text(submission_id).strip()
        if not normalized_id:
            return None
        for item in self._submissions:
            if _text(item.get("submission_id") or item.get("id")) == normalized_id:
                return deepcopy(item)
        return None

    def find_submission_for_identity(self, questionnaire_id: int, identity: dict[str, Any]) -> dict[str, Any] | None:
        candidates = _identity_lookup_values(identity)
        if not candidates:
            return None
        for item in reversed(self._submissions):
            if int(item.get("questionnaire_id") or 0) != int(questionnaire_id):
                continue
            respondent_identity = item.get("respondent_identity") if isinstance(item.get("respondent_identity"), dict) else {}
            if any(
                _text(item.get(field) or respondent_identity.get(field) or (item.get("mobile_snapshot") if field == "mobile" else "")) == value
                for field, value in candidates
            ):
                return deepcopy(item)
        return None

    def latest_submission(self, questionnaire_id: int) -> dict[str, Any] | None:
        for item in reversed(self._submissions):
            if int(item.get("questionnaire_id") or 0) == int(questionnaire_id):
                return deepcopy(item)
        return None

    def export_submissions(self, questionnaire_id: int) -> dict[str, Any] | None:
        if not self.get_questionnaire(questionnaire_id):
            return None
        rows = [item for item in self._submissions if int(item.get("questionnaire_id") or 0) == int(questionnaire_id)]
        return {
            "filename": f"questionnaire_{questionnaire_id}_submissions.json",
            "items": deepcopy(rows),
            "total": len(rows),
            "format": "json",
        }

    def get_app_setting(self, key: str) -> str | None:
        return None

    def list_external_push_log_threads(
        self,
        questionnaire_id: int | None = None,
        *,
        questionnaire_title: str = "",
        user_id: str = "",
        target_url: str = "",
        status: str = "",
        limit: int | None = 50,
    ) -> list[dict[str, Any]]:
        title_filter = _text(questionnaire_title).strip()
        user_filter = _text(user_id).strip()
        target_filter = _text(target_url).strip()
        rows = []
        for row in self._external_push_logs:
            if questionnaire_id is not None and int(row.get("questionnaire_id") or 0) != int(questionnaire_id):
                continue
            if title_filter and title_filter not in _text(row.get("questionnaire_title_snapshot")):
                continue
            if user_filter and user_filter not in _text(row.get("user_id")):
                continue
            if target_filter and target_filter not in _text(row.get("target_url")):
                continue
            rows.append(deepcopy(row))
        return _external_push_log_threads(rows, status=status, limit=limit)

    def count_external_push_logs(
        self,
        *,
        questionnaire_id: int | None = None,
        questionnaire_title: str = "",
        user_id: str = "",
        target_url: str = "",
        status: str = "",
        created_at_gte: str = "",
    ) -> int:
        title_filter = _text(questionnaire_title).strip()
        user_filter = _text(user_id).strip()
        target_filter = _text(target_url).strip()
        status_filter = _text(status).strip()
        since_filter = _text(created_at_gte).strip()
        total = 0
        for row in self._external_push_logs:
            normalized = _normalized_external_push_log(deepcopy(row))
            if questionnaire_id is not None and int(normalized.get("questionnaire_id") or 0) != int(questionnaire_id):
                continue
            if title_filter and title_filter not in _text(normalized.get("questionnaire_title_snapshot")):
                continue
            if user_filter and user_filter not in _text(normalized.get("user_id")):
                continue
            if target_filter and target_filter not in _text(normalized.get("target_url")):
                continue
            if status_filter and _text(normalized.get("status")) != status_filter:
                continue
            if since_filter and _text(normalized.get("created_at")) < since_filter:
                continue
            total += 1
        return total

    def summarize_external_push_logs(self, questionnaire_id: int) -> dict[str, Any]:
        rows = [
            _normalized_external_push_log(deepcopy(row)) for row in self._external_push_logs if int(row.get("questionnaire_id") or 0) == int(questionnaire_id)
        ]
        return {
            "total_count": len(rows),
            "success_count": sum(1 for row in rows if row.get("status") == "success"),
            "failed_count": sum(1 for row in rows if row.get("status") == "failed"),
            "last_created_at": max((_text(row.get("created_at")) for row in rows), default=""),
        }
