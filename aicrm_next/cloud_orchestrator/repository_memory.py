from __future__ import annotations

from typing import Any

from .repository import (
    _CAMPAIGN_OPEN_JOB_STATUSES,
    _agent_plan_id,
    _content_payload_for_package,
    _limit,
    _message_view,
    _now,
    _offset,
    _plan_broadcast_idempotency_key,
    _plan_view,
    _recipient_broadcast_execution_id,
    _recipient_view,
    _text,
    copy,
)


class InMemoryCloudPlanRepository:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        now = _now()
        self.plans = [
            {
                "id": 1,
                "plan_id": "plan_probe",
                "display_name": "1.6.3 触达赵言方",
                "intent": "1.6.3 触达赵言方",
                "owner_userid": "HuangYouCan",
                "candidate_count": 2,
                "review_status": "pending_review",
                "run_status": "draft",
                "status": "draft",
                "selection_json": {"owner_userid": "HuangYouCan"},
                "updated_at": now,
            }
        ]
        self.recipients = [
            {"id": 1, "plan_id": "plan_probe", "unionid": "union_plan_a", "external_userid": "wm_a", "owner_userid": "HuangYouCan", "display_name": "赵言方", "planned_message_count": 1, "approval_status": "pending", "send_status": "pending", "updated_at": now},
            {"id": 2, "plan_id": "plan_probe", "unionid": "union_plan_b", "external_userid": "wm_b", "owner_userid": "HuangYouCan", "display_name": "黄永灿", "planned_message_count": 1, "approval_status": "pending", "send_status": "pending", "updated_at": now},
        ]
        self.messages = [
            {"id": 1, "plan_id": "plan_probe", "recipient_id": 1, "unionid": "union_plan_a", "external_userid": "wm_a", "sequence_index": 1, "day_offset": 0, "send_time": "10:00", "content_text": "你好", "content_payload_json": {}, "attachments_json": [], "status": "pending"},
            {"id": 2, "plan_id": "plan_probe", "recipient_id": 2, "unionid": "union_plan_b", "external_userid": "wm_b", "sequence_index": 1, "day_offset": 0, "send_time": "10:00", "content_text": "你好", "content_payload_json": {}, "attachments_json": [], "status": "pending"},
        ]
        self.legacy_plans = [
            {
                "id": 10,
                "plan_id": "standard_subscription_20260530_1000_zhaoyanfang_v1",
                "display_name": "Standard 订阅 v1.6.3 触达 · ZhaoYanFang · 2026-05-30 10:00",
                "intent": "Standard 订阅 v1.6.3 触达",
                "owner_userid": "ZhaoYanFang",
                "candidate_count": 3,
                "review_status": "approved",
                "run_status": "active",
                "status": "draft",
                "selection_json": {"group_code": "standard_subscription_20260530_1000_zhaoyanfang_v1"},
                "updated_at": now,
                "source_type": "legacy_campaign",
            }
        ]
        self.legacy_recipients = [
            {"id": -11, "plan_id": "standard_subscription_20260530_1000_zhaoyanfang_v1", "external_userid": "wm_legacy_a", "owner_userid": "ZhaoYanFang", "display_name": "老客户 A", "planned_message_count": 2, "approval_status": "approved", "send_status": "queued", "updated_at": now, "source_type": "legacy_campaign", "supports_recipient_approval": False},
            {"id": -12, "plan_id": "standard_subscription_20260530_1000_zhaoyanfang_v1", "external_userid": "wm_legacy_b", "owner_userid": "ZhaoYanFang", "display_name": "老客户 B", "planned_message_count": 2, "approval_status": "approved", "send_status": "queued", "updated_at": now, "source_type": "legacy_campaign", "supports_recipient_approval": False},
            {"id": -13, "plan_id": "standard_subscription_20260530_1000_zhaoyanfang_v1", "external_userid": "wm_legacy_c", "owner_userid": "ZhaoYanFang", "display_name": "老客户 C", "planned_message_count": 2, "approval_status": "approved", "send_status": "sent", "updated_at": now, "source_type": "legacy_campaign", "supports_recipient_approval": False},
        ]
        self.legacy_messages = [
            {"id": -101, "recipient_id": -11, "sequence_index": 1, "day_offset": 0, "send_time": "10:00", "content_text": "老话术 1", "content_payload_json": {}, "attachments_json": [], "status": "pending", "source_type": "legacy_campaign"},
            {"id": -102, "recipient_id": -11, "sequence_index": 2, "day_offset": 1, "send_time": "10:00", "content_text": "老话术 2", "content_payload_json": {}, "attachments_json": [], "status": "pending", "source_type": "legacy_campaign"},
        ]
        self.broadcast_jobs: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []

    def _resolve_fixture_unionid_by_external_userid(self, external_userid: str) -> str:
        normalized_external_userid = _text(external_userid)
        if not normalized_external_userid:
            return ""
        for recipient in self.recipients:
            if _text(recipient.get("external_userid")) == normalized_external_userid:
                return _text(recipient.get("unionid"))
        suffix = normalized_external_userid
        for prefix in ("wm_", "external_"):
            if suffix.startswith(prefix):
                suffix = suffix[len(prefix) :]
                break
        return f"union_{suffix}" if suffix else ""

    def _stats(self, plan_id: str) -> dict[str, int]:
        rows = [item for item in [*self.recipients, *self.legacy_recipients] if item["plan_id"] == plan_id]
        return {
            "target_count": len(rows),
            "approved_count": sum(1 for item in rows if item.get("approval_status") == "approved"),
            "pending_count": sum(1 for item in rows if item.get("approval_status") == "pending"),
            "rejected_count": sum(1 for item in rows if item.get("approval_status") == "rejected"),
            "sent_count": sum(1 for item in rows if item.get("send_status") == "sent"),
            "failed_count": sum(1 for item in rows if item.get("send_status") == "failed"),
        }

    def list_plans(self, *, status: str = "", keyword: str = "", limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        legacy_plan_ids = {item["plan_id"] for item in self.legacy_plans}
        materialized_cloud_plan_ids = {item["plan_id"] for item in self.recipients}
        cloud_rows = [
            item
            for item in self.plans
            if item["plan_id"] in materialized_cloud_plan_ids or item["plan_id"] not in legacy_plan_ids
        ]
        legacy_rows = [item for item in self.legacy_plans if item["plan_id"] not in materialized_cloud_plan_ids]
        rows = [item for item in [*cloud_rows, *legacy_rows] if (not status or item.get("review_status") == status or item.get("status") == status or item.get("run_status") == status)]
        if keyword:
            rows = [item for item in rows if keyword.lower() in (item.get("display_name", "") + item.get("plan_id", "")).lower()]
        total = len(rows)
        rows = rows[_offset(offset) : _offset(offset) + _limit(limit, default=20, maximum=100)]
        return [_plan_view(copy.deepcopy(item), self._stats(item["plan_id"])) for item in rows], total

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        materialized_cloud_plan_ids = {item["plan_id"] for item in self.recipients}
        if plan_id not in materialized_cloud_plan_ids:
            for item in self.legacy_plans:
                if item["plan_id"] == plan_id:
                    return _plan_view(copy.deepcopy(item), self._stats(plan_id))
        for item in self.plans:
            if item["plan_id"] == plan_id:
                return _plan_view(copy.deepcopy(item), self._stats(plan_id))
        for item in self.legacy_plans:
            if item["plan_id"] == plan_id:
                return _plan_view(copy.deepcopy(item), self._stats(plan_id))
        return None

    def plan_stats(self, plan_id: str) -> dict[str, int]:
        return self._stats(plan_id)

    def list_recipients(self, plan_id: str, *, status: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = [item for item in [*self.recipients, *self.legacy_recipients] if item["plan_id"] == plan_id and (not status or item.get("approval_status") == status or item.get("send_status") == status)]
        total = len(rows)
        rows = rows[_offset(offset) : _offset(offset) + _limit(limit, default=50, maximum=200)]
        return [_recipient_view(copy.deepcopy(item)) for item in rows], total

    def get_recipient(self, plan_id: str, recipient_id: int) -> dict[str, Any] | None:
        for item in [*self.recipients, *self.legacy_recipients]:
            if item["plan_id"] == plan_id and int(item["id"]) == int(recipient_id):
                return _recipient_view(copy.deepcopy(item))
        return None

    def list_recipient_messages(self, recipient_id: int) -> list[dict[str, Any]]:
        return [_message_view(copy.deepcopy(item)) for item in [*self.messages, *self.legacy_messages] if int(item["recipient_id"]) == int(recipient_id)]

    def approve_plan(self, plan_id: str, *, operator: str) -> dict[str, Any] | None:
        for item in self.plans:
            if item["plan_id"] == plan_id:
                if item.get("review_status") == "rejected":
                    raise ValueError("plan is rejected")
                item["review_status"] = "approved"
                item["updated_at"] = _now()
                self.audits.append({"action_type": "cloud_plan_approve", "target_id": plan_id, "operator": operator})
                return self.get_plan(plan_id)
        for item in self.legacy_plans:
            if item["plan_id"] == plan_id:
                if item.get("review_status") == "rejected":
                    raise ValueError("plan is rejected")
                item["review_status"] = "approved"
                item["run_status"] = "active"
                item["status"] = "active"
                item["updated_at"] = _now()
                queued = 0
                for recipient in self.legacy_recipients:
                    if recipient["plan_id"] == plan_id and recipient.get("send_status") == "pending":
                        recipient["approval_status"] = "approved"
                        recipient["send_status"] = "queued"
                        recipient["updated_at"] = _now()
                        queued += 1
                if queued:
                    self.broadcast_jobs.append(
                        {
                            "id": len(self.broadcast_jobs) + 1,
                            "source_type": "campaign",
                            "source_table": "campaign_members",
                            "source_id": f"{plan_id}:legacy",
                            "status": "queued",
                            "scheduled_for": _now(),
                            "target_count": queued,
                        }
                    )
                self.audits.append({"action_type": "legacy_campaign_group_approve_and_start_from_cloud_plan", "target_id": plan_id, "operator": operator})
                return self.get_plan(plan_id)
        return None

    def reject_plan(self, plan_id: str, *, operator: str, reason: str = "") -> dict[str, Any] | None:
        for item in self.plans:
            if item["plan_id"] == plan_id:
                item["review_status"] = "rejected"
                item["status"] = "rejected"
                item["updated_at"] = _now()
                self.audits.append({"action_type": "cloud_plan_reject", "target_id": plan_id, "operator": operator, "reason": reason})
                return self.get_plan(plan_id)
        for item in self.legacy_plans:
            if item["plan_id"] == plan_id:
                item["review_status"] = "rejected"
                item["run_status"] = "cancelled"
                item["status"] = "cancelled"
                item["updated_at"] = _now()
                cancelled_members = 0
                for recipient in self.legacy_recipients:
                    if recipient["plan_id"] == plan_id and recipient.get("send_status") != "sent":
                        recipient["approval_status"] = "rejected"
                        recipient["send_status"] = "cancelled"
                        recipient["updated_at"] = _now()
                        cancelled_members += 1
                cancelled_jobs = 0
                for job in self.broadcast_jobs:
                    if job.get("source_type") == "campaign" and job.get("source_id") == f"{plan_id}:legacy" and job.get("status") in _CAMPAIGN_OPEN_JOB_STATUSES:
                        job["status"] = "cancelled"
                        job["cancelled_by"] = operator
                        job["cancel_reason"] = reason or "cloud plan rejected"
                        job["cancelled_at"] = _now()
                        cancelled_jobs += 1
                self.audits.append(
                    {
                        "action_type": "legacy_campaign_group_reject_from_cloud_plan",
                        "target_id": plan_id,
                        "operator": operator,
                        "reason": reason,
                        "cancelled_members": cancelled_members,
                        "cancelled_jobs": cancelled_jobs,
                    }
                )
                return self.get_plan(plan_id)
        return None

    def create_or_reuse_recipient_broadcast_jobs(
        self,
        plan_id: str,
        *,
        operator: str,
        source_event_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        normalized_plan_id = _text(plan_id)
        if not normalized_plan_id:
            return {"status": "skipped", "reason": "missing_plan_id"}
        plan = self.get_plan(normalized_plan_id)
        if not plan:
            return {"status": "skipped", "reason": "missing_plan_id"}
        if _text(plan.get("source_type")) and _text(plan.get("source_type")) != "cloud_plan":
            return {"status": "skipped", "reason": "unsupported_plan_type", "plan_type": _text(plan.get("source_type"))}
        if _text(plan.get("review_status")) not in {"approved", "reviewing"}:
            return {"status": "skipped", "reason": "unsupported_plan_type", "review_status": _text(plan.get("review_status"))}
        recipients = [
            item
            for item in self.recipients
            if item["plan_id"] == normalized_plan_id
            and item.get("approval_status") != "rejected"
            and item.get("send_status") not in {"cancelled", "sent"}
            and _text(item.get("unionid"))
            and any(
                message.get("plan_id") == normalized_plan_id
                and int(message.get("recipient_id") or 0) == int(item.get("id") or 0)
                and message.get("status") != "cancelled"
                for message in self.messages
            )
        ]
        if not recipients:
            return {"status": "skipped", "reason": "missing_audience"}
        planner_idempotency_key = _plan_broadcast_idempotency_key(
            normalized_plan_id,
            source_event_id=source_event_id,
            idempotency_key=idempotency_key,
        )
        job_ids: list[int] = []
        created_count = 0
        reused_count = 0
        for recipient in recipients:
            recipient_id = int(recipient["id"])
            recipient_idempotency_key = f"cloud_plan_recipient:{normalized_plan_id}:{recipient_id}"
            execution_id = _recipient_broadcast_execution_id(recipient_idempotency_key)
            existing = next(
                (
                    item
                    for item in self.broadcast_jobs
                    if item.get("idempotency_key") == recipient_idempotency_key
                ),
                None,
            )
            if existing:
                job_id = int(existing["id"])
                if not _text(existing.get("execution_id")):
                    existing["execution_id"] = execution_id
                reused_count += 1
            else:
                job_id = len(self.broadcast_jobs) + 1
                self.broadcast_jobs.append(
                    {
                        "id": job_id,
                        "source_type": "cloud_plan",
                        "source_table": "cloud_broadcast_plan_recipients",
                        "source_id": f"{normalized_plan_id}:{recipient_id}",
                        "scheduled_for": _now(),
                        "priority": 100,
                        "batch_key": f"cloud_plan_recipient:{normalized_plan_id}",
                        "business_domain": "ai_assistant",
                        "idempotency_key": recipient_idempotency_key,
                        "execution_id": execution_id,
                        "channel": "wecom_private",
                        "target_kind": "unionid",
                        "status": "queued",
                        "requires_approval": False,
                        "target_unionids_json": [_text(recipient["unionid"])],
                        "target_count": 1,
                        "target_summary": _text(recipient.get("display_name")) or _text(recipient["unionid"]),
                        "content_type": "cloud_plan",
                        "content_payload": {
                            "plan_id": normalized_plan_id,
                            "recipient_id": recipient_id,
                            "unionid": _text(recipient["unionid"]),
                            "message_mode": "recipient_messages",
                        },
                        "content_summary": f"{_text(plan.get('display_name')) or _text(plan.get('intent')) or normalized_plan_id} · {_text(recipient.get('display_name')) or _text(recipient['unionid'])}",
                        "trace_id": normalized_plan_id,
                        "created_by": _text(operator) or "internal_event_worker",
                        "created_at": _now(),
                        "updated_at": _now(),
                        "metadata_json": {
                            "planner_consumer": "broadcast_task_planner_consumer",
                            "source_event_id": _text(source_event_id),
                            "plan_idempotency_key": planner_idempotency_key,
                            "duplicate_policy": "reuse_recipient_idempotency_key",
                        },
                    }
                )
                created_count += 1
            job_ids.append(job_id)
            recipient.update(
                {
                    "approval_status": "approved",
                    "send_status": "queued" if recipient.get("send_status") == "pending" else recipient.get("send_status"),
                    "approved_by": recipient.get("approved_by") or operator,
                    "approved_at": recipient.get("approved_at") or _now(),
                    "broadcast_job_id": recipient.get("broadcast_job_id") or job_id,
                    "updated_at": _now(),
                }
            )
            for message in self.messages:
                if message.get("plan_id") == normalized_plan_id and int(message.get("recipient_id") or 0) == recipient_id and message.get("status") == "pending":
                    message["status"] = "queued"
        self.audits.append(
            {
                "action_type": "ops_plan_recipient_broadcast_jobs_plan",
                "target_id": normalized_plan_id,
                "operator": operator,
                "plan_id": normalized_plan_id,
                "broadcast_job_count": len(set(job_ids)),
                "created_count": created_count,
                "reused_count": reused_count,
            }
        )
        first_job_id = job_ids[0] if job_ids else 0
        return {
            "status": "created" if created_count else "reused",
            "broadcast_job_id": first_job_id,
            "broadcast_job_count": len(set(job_ids)),
            "created_count": created_count,
            "reused_count": reused_count,
            "idempotency_key": planner_idempotency_key,
            "target_count": len(recipients),
            "source_id": normalized_plan_id,
            "trace_id": normalized_plan_id,
            "downstream_status": "broadcast_job_queued",
            "push_center_job_id": f"broadcast_job:{first_job_id}" if first_job_id else "",
        }

    def create_or_reuse_plan_broadcast_job(
        self,
        plan_id: str,
        *,
        operator: str,
        source_event_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return self.create_or_reuse_recipient_broadcast_jobs(
            plan_id,
            operator=operator,
            source_event_id=source_event_id,
            idempotency_key=idempotency_key,
        )

    def create_or_reuse_agent_send_plan(
        self,
        *,
        external_event_id: str,
        package_key: str,
        external_userid: str,
        owner_userid: str,
        content_package: dict[str, Any],
        operator: str,
        requires_review: bool = False,
    ) -> dict[str, Any]:
        normalized_event_id = _text(external_event_id)
        normalized_external_userid = _text(external_userid)
        normalized_owner = _text(owner_userid)
        if not normalized_event_id:
            return {"status": "skipped", "reason": "missing_external_event_id"}
        if not normalized_external_userid:
            return {"status": "skipped", "reason": "missing_external_userid"}
        if not normalized_owner:
            return {"status": "skipped", "reason": "missing_owner_userid"}
        normalized_unionid = self._resolve_fixture_unionid_by_external_userid(normalized_external_userid)
        if not normalized_unionid:
            return {"status": "skipped", "reason": "identity_pending_unionid"}
        plan_id = _agent_plan_id(normalized_event_id)
        review_status = "pending_review" if requires_review else "approved"
        approval_status = "pending" if requires_review else "approved"
        existing = next((item for item in self.plans if item.get("plan_id") == plan_id), None)
        if not existing:
            self.plans.append(
                {
                    "id": len(self.plans) + len(self.legacy_plans) + 1,
                    "plan_id": plan_id,
                    "display_name": f"Agent 生成待发送计划 · {normalized_external_userid}",
                    "intent": f"Agent generated send plan {normalized_external_userid}",
                    "owner_userid": normalized_owner,
                    "candidate_count": 1,
                    "review_status": review_status,
                    "run_status": "draft",
                    "status": "draft",
                    "selection_json": {
                        "source": "automation_agent",
                        "package_key": _text(package_key),
                        "external_event_id": normalized_event_id,
                        "unionid": normalized_unionid,
                    },
                    "updated_at": _now(),
                    "source_type": "cloud_plan",
                }
            )
        recipient = next(
            (
                item
                for item in self.recipients
                if item.get("plan_id") == plan_id and item.get("unionid") == normalized_unionid
            ),
            None,
        )
        if recipient is None:
            recipient = {
                "id": len(self.recipients) + 1,
                "plan_id": plan_id,
                "unionid": normalized_unionid,
                "external_userid": normalized_external_userid,
                "owner_userid": normalized_owner,
                "display_name": normalized_unionid,
                "planned_message_count": 1,
                "approval_status": approval_status,
                "send_status": "pending",
                "updated_at": _now(),
            }
            self.recipients.append(recipient)
        content_payload = _content_payload_for_package(content_package)
        message = next(
            (
                item
                for item in self.messages
                if item.get("plan_id") == plan_id and int(item.get("recipient_id") or 0) == int(recipient["id"]) and int(item.get("sequence_index") or 0) == 1
            ),
            None,
        )
        if message is None:
            message = {
                "id": len(self.messages) + 1,
                "plan_id": plan_id,
                "recipient_id": int(recipient["id"]),
                "unionid": normalized_unionid,
                "external_userid": normalized_external_userid,
                "sequence_index": 1,
                "day_offset": 0,
                "send_time": "",
                "content_text": _text(content_package.get("content_text")),
                "content_payload_json": content_payload,
                "attachments_json": [],
                "status": "pending",
            }
            self.messages.append(message)
        else:
            message["content_text"] = _text(content_package.get("content_text"))
            message["content_payload_json"] = content_payload
        self.audits.append(
            {
                "action_type": "automation_agent_enqueue_send_plan",
                "target_id": plan_id,
                "operator": operator,
                "external_event_id": normalized_event_id,
            }
        )
        return {
            "status": "reused" if existing else "created",
            "plan_id": plan_id,
            "recipient_id": int(recipient["id"]),
            "message_id": int(message["id"]),
            "downstream_status": "send_plan_pending",
            "push_center_job_id": f"cloud_plan:{plan_id}",
        }

    def approve_recipient(self, plan_id: str, recipient_id: int, *, operator: str) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if not plan:
            raise LookupError("plan not found")
        if plan["review_status"] == "rejected":
            raise ValueError("plan is rejected")
        if plan["review_status"] not in {"approved", "reviewing"}:
            raise ValueError("plan is not approved for recipient review")
        recipient = next((item for item in self.recipients if item["plan_id"] == plan_id and int(item["id"]) == int(recipient_id)), None)
        if not recipient:
            raise LookupError("recipient not found")
        if recipient.get("approval_status") == "rejected":
            raise ValueError("recipient is rejected")
        source_id = f"{plan_id}:{int(recipient_id)}"
        existing = next((item for item in self.broadcast_jobs if item["source_id"] == source_id), None)
        if existing:
            status = "already_approved"
            job_id = existing["id"]
        else:
            job_id = len(self.broadcast_jobs) + 1
            self.broadcast_jobs.append(
                {
                    "id": job_id,
                    "source_type": "cloud_plan",
                    "source_table": "cloud_broadcast_plan_recipients",
                    "source_id": source_id,
                    "target_unionids_json": [recipient["unionid"]],
                    "target_count": 1,
                    "content_payload": {"plan_id": plan_id, "recipient_id": int(recipient_id), "unionid": recipient["unionid"], "message_mode": "recipient_messages"},
                    "idempotency_key": f"cloud_plan_recipient:{plan_id}:{int(recipient_id)}",
                }
            )
            status = "approved"
        recipient.update({"approval_status": "approved", "send_status": "queued", "approved_by": operator, "approved_at": _now(), "broadcast_job_id": job_id, "updated_at": _now()})
        for message in self.messages:
            if int(message["recipient_id"]) == int(recipient_id) and message.get("status") == "pending":
                message["status"] = "queued"
        self.audits.append({"action_type": "cloud_plan_recipient_approve", "target_id": source_id, "operator": operator})
        return {"status": status, "recipient": _recipient_view(copy.deepcopy(recipient)), "job_id": job_id}

    def reject_recipient(self, plan_id: str, recipient_id: int, *, operator: str, reason: str = "") -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if not plan:
            raise LookupError("plan not found")
        if plan["review_status"] == "rejected":
            raise ValueError("plan is rejected")
        recipient = next((item for item in self.recipients if item["plan_id"] == plan_id and int(item["id"]) == int(recipient_id)), None)
        if not recipient:
            raise LookupError("recipient not found")
        if recipient.get("send_status") == "sent":
            raise ValueError("sent recipient cannot be rejected")
        recipient.update({"approval_status": "rejected", "send_status": "cancelled", "rejected_by": operator, "rejected_at": _now(), "reject_reason": reason, "updated_at": _now()})
        self.audits.append({"action_type": "cloud_plan_recipient_reject", "target_id": f"{plan_id}:{int(recipient_id)}", "operator": operator})
        return {"status": "rejected", "recipient": _recipient_view(copy.deepcopy(recipient))}

    def update_recipient_message(
        self,
        plan_id: str,
        recipient_id: int,
        message_id: int,
        *,
        content_package: dict[str, Any],
        day_offset: Any = None,
        send_time: Any = None,
        operator: str,
    ) -> dict[str, Any]:
        if int(recipient_id) < 0:
            plan = self.get_plan(plan_id)
            if not plan:
                raise LookupError("plan not found")
            if plan["review_status"] == "rejected":
                raise ValueError("plan is rejected")
            recipient = next((item for item in self.legacy_recipients if item["plan_id"] == plan_id and int(item["id"]) == int(recipient_id)), None)
            if not recipient:
                raise LookupError("recipient not found")
            if recipient.get("approval_status") != "pending" or recipient.get("send_status") != "pending":
                raise ValueError("recipient is not editable")
            message = next((item for item in self.legacy_messages if int(item["recipient_id"]) == int(recipient_id) and int(item["id"]) == int(message_id)), None)
            if not message:
                raise LookupError("message not found")
            if message.get("status") != "pending":
                raise ValueError("message is not editable")
            try:
                normalized_day_offset = max(0, int(day_offset if day_offset is not None else message.get("day_offset") or 0))
            except (TypeError, ValueError):
                normalized_day_offset = int(message.get("day_offset") or 0)
            content_payload = _content_payload_for_package(content_package)
            message.update(
                {
                    "content_text": _text(content_package.get("content_text")),
                    "content_payload_json": content_payload,
                    "attachments_json": [],
                    "day_offset": normalized_day_offset,
                    "send_time": _text(send_time) or _text(message.get("send_time")),
                    "updated_at": _now(),
                }
            )
            recipient["updated_at"] = _now()
            self.audits.append({"action_type": "legacy_campaign_step_update_from_cloud_plan", "target_id": f"{plan_id}:{int(recipient_id)}:{int(message_id)}", "operator": operator})
            return {
                "status": "updated",
                "recipient": _recipient_view(copy.deepcopy(recipient)),
                "message": _message_view(copy.deepcopy(message)),
            }
        plan = self.get_plan(plan_id)
        if not plan:
            raise LookupError("plan not found")
        if plan["review_status"] == "rejected":
            raise ValueError("plan is rejected")
        recipient = next((item for item in self.recipients if item["plan_id"] == plan_id and int(item["id"]) == int(recipient_id)), None)
        if not recipient:
            raise LookupError("recipient not found")
        if recipient.get("approval_status") != "pending" or recipient.get("send_status") != "pending":
            raise ValueError("recipient is not editable")
        message = next((item for item in self.messages if int(item["recipient_id"]) == int(recipient_id) and int(item["id"]) == int(message_id)), None)
        if not message:
            raise LookupError("message not found")
        if message.get("status") != "pending":
            raise ValueError("message is not editable")
        try:
            normalized_day_offset = max(0, int(day_offset if day_offset is not None else message.get("day_offset") or 0))
        except (TypeError, ValueError):
            normalized_day_offset = int(message.get("day_offset") or 0)
        content_payload = _content_payload_for_package(content_package)
        message.update(
            {
                "content_text": _text(content_package.get("content_text")),
                "content_payload_json": content_payload,
                "attachments_json": [],
                "day_offset": normalized_day_offset,
                "send_time": _text(send_time) or _text(message.get("send_time")),
                "updated_at": _now(),
            }
        )
        recipient["updated_at"] = _now()
        self.audits.append({"action_type": "cloud_plan_recipient_message_update", "target_id": f"{plan_id}:{int(recipient_id)}:{int(message_id)}", "operator": operator})
        return {
            "status": "updated",
            "recipient": _recipient_view(copy.deepcopy(recipient)),
            "message": _message_view(copy.deepcopy(message)),
        }
