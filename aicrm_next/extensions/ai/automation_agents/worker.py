from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlparse

from aicrm_next.engagement.send_content.application import normalize_send_content_package
from aicrm_next.extensions.ai.ai_audience_ops.webhook_service import AudienceInboundWebhookService
from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.llm_output_guard import looks_like_prompt_output

from .context_builder import build_agent_context, referenced_context_keys, render_chinese_placeholders
from .repository import AutomationAgentRepository, _safe_int, _text, build_automation_agent_repository


def _package_key_from_send_webhook_url(value: str) -> str:
    raw = _text(value)
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme else raw.split("?", 1)[0]
    prefix = "/api/ai/audience/packages/"
    suffix = "/webhook"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return ""
    return unquote(path[len(prefix) : -len(suffix)].strip("/"))


class AutomationAgentWorker:
    """Durable per-recipient preparation; provider generation is never inline."""

    def __init__(self, repository: AutomationAgentRepository | None = None) -> None:
        self._repo = repository or build_automation_agent_repository()

    def run_batch_and_enqueue_broadcast_jobs(
        self,
        batch_id: str,
        *,
        operator: str = "automation_agent_queue_bridge",
        parent_execution_id: str = "",
    ) -> dict[str, Any]:
        result = self._repo.enqueue_batch_item_prepare_events(
            batch_id,
            operator=operator,
            parent_execution_id=parent_execution_id,
        )
        return {
            "ok": bool(result.get("ok")),
            "batch_id": _text(batch_id),
            "batch": result,
            "broadcast_enqueue": {
                "ok": True,
                "status": "per_item_pipeline_queued",
                "approved_count": 0,
                "failed_count": 0,
            },
            "real_external_call_executed": False,
        }

    def run_batch(self, batch_id: str) -> dict[str, Any]:
        return self.run_batch_and_enqueue_broadcast_jobs(batch_id)["batch"]

    def run_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.prepare_item(int(item.get("id") or 0))

    def prepare_item(
        self,
        item_id: int,
        *,
        source_event_id: str = "",
        parent_execution_id: str = "",
    ) -> dict[str, Any]:
        item = self._repo.claim_item_for_prepare(item_id)
        if not item:
            return {"ok": False, "item_id": int(item_id), "error": "item_not_found"}
        status = _text(item.get("status"))
        if not bool(item.get("pipeline_claimed")):
            if status in {"generation_queued", "generation_succeeded", "send_plan_created", "callback_succeeded"}:
                return {
                    "ok": True,
                    "deduplicated": True,
                    "item_id": int(item_id),
                    "status": status,
                    "external_effect_job_id": _safe_int(item.get("generation_effect_job_id")),
                    "real_external_call_executed": False,
                }
            return {
                "ok": False,
                "deduplicated": True,
                "item_id": int(item_id),
                "status": status,
                "error": _text(item.get("error_code")) or "item_not_claimable",
                "detail": _text(item.get("error_message")),
                "real_external_call_executed": False,
            }

        agent = (
            dict(item.get("agent_config_snapshot_json") or {})
            if isinstance(item.get("agent_config_snapshot_json"), dict)
            else {}
        )
        if not agent or _text(agent.get("agent_code")) != _text(item.get("agent_code")):
            return self._fail(item_id, "agent_snapshot_missing", "frozen agent configuration is required")
        if _text(agent.get("status")) != "active":
            return self._fail(item_id, "agent_snapshot_not_active", "frozen agent configuration is not active")
        unionid = _text(item.get("unionid"))
        external_userid = self._repo.resolve_external_userid_for_unionid(unionid) if unionid else ""
        if not external_userid:
            return self._fail(item_id, "identity_external_userid_missing", "primary_external_userid is required")

        automation_type = _text(agent.get("automation_type")) or "agent"
        role_prompt = _text(agent.get("published_role_prompt"))
        task_prompt = _text(agent.get("published_task_prompt"))
        keys = set() if automation_type == "fixed_script" else referenced_context_keys(role_prompt, task_prompt)
        try:
            context = build_agent_context(
                external_userid,
                keys,
                agent_code=_text(item.get("agent_code")),
                batch_id=_text(item.get("batch_id")),
                external_event_id=_text(item.get("external_event_id")),
                repository=self._repo,
            )
        except Exception as exc:
            return self._fail(
                item_id,
                "context_build_failed",
                str(exc),
                retryable=True,
            )
        owner_userid = _text(context.get("owner_userid"))
        if not owner_userid:
            return self._fail(
                item_id,
                "failed_owner_missing",
                "owner_userid is required",
                context=context,
            )
        if bool(agent.get("need_human_review")):
            return self._fail(
                item_id,
                "human_review_required",
                "agent requires human review before customer send",
                context=context,
                owner_userid=owner_userid,
            )
        if automation_type == "fixed_script":
            return self._prepare_fixed_script(
                item,
                agent,
                external_userid=external_userid,
                owner_userid=owner_userid,
                context=context,
                parent_execution_id=parent_execution_id,
            )

        rendered_role = render_chinese_placeholders(role_prompt, context.get("blocks") or {})
        rendered_task = render_chinese_placeholders(task_prompt, context.get("blocks") or {})
        prompt_preview = f"{rendered_role}\n\n{rendered_task}"[:2000]
        result = self._repo.plan_generation_effect(
            item_id,
            payload={
                "item_id": int(item_id),
                "batch_id": _text(item.get("batch_id")),
                "agent_code": _text(agent.get("agent_code")),
                "agent_published_version": _safe_int(item.get("agent_published_version")),
                "role_prompt": rendered_role,
                "task_prompt": rendered_task,
                "raw_role_prompt": role_prompt,
                "raw_task_prompt": task_prompt,
                "variables": {
                    "external_userid": external_userid,
                    "context_keys": sorted(keys),
                },
                "external_userid": external_userid,
                "owner_userid": owner_userid,
                "context_snapshot": context,
                "prompt_preview": prompt_preview,
            },
            payload_summary={
                "item_id": int(item_id),
                "batch_id": _text(item.get("batch_id")),
                "agent_code": _text(agent.get("agent_code")),
                "agent_published_version": _safe_int(item.get("agent_published_version")),
                "role_prompt_chars": len(rendered_role),
                "task_prompt_chars": len(rendered_task),
                "context_key_count": len(keys),
            },
            parent_execution_id=parent_execution_id,
            source_event_id=source_event_id,
        )
        return {**result, "status": "generation_queued" if result.get("ok") else "failed_retryable"}

    def complete_generation(
        self,
        *,
        item_id: int,
        generation_effect_job_id: int,
        final_text: str,
    ) -> dict[str, Any]:
        item = self._repo.get_pipeline_item(item_id)
        if not item:
            return {"ok": False, "error": "item_not_found", "item_id": int(item_id)}
        if _safe_int(item.get("generation_effect_job_id")) != int(generation_effect_job_id):
            return {"ok": False, "error": "generation_effect_link_mismatch", "item_id": int(item_id)}
        if _text(item.get("status")) == "send_plan_created":
            return {"ok": True, "deduplicated": True, "item_id": int(item_id)}
        agent = (
            dict(item.get("agent_config_snapshot_json") or {})
            if isinstance(item.get("agent_config_snapshot_json"), dict)
            else {}
        )
        output = _text(final_text)
        context = dict(item.get("context_snapshot_json") or {})
        owner_userid = _text(item.get("owner_userid"))
        customer = context.get("customer") if isinstance(context.get("customer"), dict) else {}
        external_userid = _text(context.get("external_userid") or customer.get("external_userid"))
        if not context or not owner_userid or not external_userid:
            # The canonical generation payload also contains these values, but
            # the prepare transaction persists them only at terminal delivery.
            # Rebuild from the same frozen snapshot when the item first completes.
            unionid = _text(item.get("unionid"))
            external_userid = self._repo.resolve_external_userid_for_unionid(unionid) if unionid else ""
            keys = referenced_context_keys(
                _text(agent.get("published_role_prompt")),
                _text(agent.get("published_task_prompt")),
            )
            context = build_agent_context(
                external_userid,
                keys,
                agent_code=_text(item.get("agent_code")),
                batch_id=_text(item.get("batch_id")),
                external_event_id=_text(item.get("external_event_id")),
                repository=self._repo,
            )
            owner_userid = _text(context.get("owner_userid"))
        role_prompt = _text(agent.get("published_role_prompt"))
        task_prompt = _text(agent.get("published_task_prompt"))
        rendered_role = render_chinese_placeholders(role_prompt, context.get("blocks") or {})
        rendered_task = render_chinese_placeholders(task_prompt, context.get("blocks") or {})
        prompt_preview = f"{rendered_role}\n\n{rendered_task}"[:2000]
        if not output:
            return {"ok": False, "error": "agent_generation_empty", "item_id": int(item_id)}
        if looks_like_prompt_output(output, role_prompt=role_prompt, task_prompt=task_prompt) or looks_like_prompt_output(
            output,
            role_prompt=rendered_role,
            task_prompt=rendered_task,
        ):
            failed = self._fail(
                item_id,
                "llm_output_rejected",
                "agent output looks like an unresolved prompt or template",
                context=context,
                owner_userid=owner_userid,
                prompt_preview=prompt_preview,
            )
            return {**failed, "ok": True, "business_outcome": "failed"}
        fixed_package = agent.get("fixed_content_package_json") if isinstance(agent.get("fixed_content_package_json"), dict) else {}
        content_package = normalize_send_content_package(
            {**fixed_package, "content_text": output},
            text_enabled=True,
            require_body=True,
        )
        return self._enqueue_callback(
            item,
            agent,
            external_userid=external_userid,
            owner_userid=owner_userid,
            context=context,
            prompt_preview=prompt_preview,
            raw_output=output,
            content_text=output,
            content_package=content_package,
            generated=True,
            parent_execution_id=f"external_effect_job:{int(generation_effect_job_id)}",
        )

    def settle_generation_failure(
        self,
        *,
        item_id: int,
        generation_effect_job_id: int,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        item = self._repo.get_pipeline_item(item_id)
        if not item:
            return {"ok": False, "error": "item_not_found", "item_id": int(item_id)}
        if _safe_int(item.get("generation_effect_job_id")) != int(generation_effect_job_id):
            return {"ok": False, "error": "generation_effect_link_mismatch", "item_id": int(item_id)}
        if _text(item.get("status")) in {"send_plan_created", "failed"}:
            return {"ok": True, "deduplicated": True, "item_id": int(item_id)}
        self._repo.fail_pipeline_item(
            item_id,
            error_code=error_code or "agent_generation_terminal",
            error_message=error_message or "AI generation did not complete successfully",
        )
        return {"ok": True, "item_id": int(item_id), "status": "failed"}

    def _prepare_fixed_script(
        self,
        item: dict[str, Any],
        agent: dict[str, Any],
        *,
        external_userid: str,
        owner_userid: str,
        context: dict[str, Any],
        parent_execution_id: str,
    ) -> dict[str, Any]:
        item_id = int(item.get("id") or 0)
        fixed_package = agent.get("fixed_content_package_json") if isinstance(agent.get("fixed_content_package_json"), dict) else {}
        try:
            content_package = normalize_send_content_package(fixed_package, text_enabled=True, require_body=True)
        except ContractError as exc:
            return self._fail(
                item_id,
                "fixed_content_missing",
                str(exc),
                context=context,
                owner_userid=owner_userid,
            )
        content_text = _text(content_package.get("content_text"))
        if not content_text:
            return self._fail(
                item_id,
                "fixed_content_missing",
                "fixed script content_text is required",
                context=context,
                owner_userid=owner_userid,
            )
        return self._enqueue_callback(
            item,
            agent,
            external_userid=external_userid,
            owner_userid=owner_userid,
            context=context,
            prompt_preview="",
            raw_output=content_text,
            content_text=content_text,
            content_package=content_package,
            generated=False,
            parent_execution_id=parent_execution_id,
        )

    def _enqueue_callback(
        self,
        item: dict[str, Any],
        agent: dict[str, Any],
        *,
        external_userid: str,
        owner_userid: str,
        context: dict[str, Any],
        prompt_preview: str,
        raw_output: str,
        content_text: str,
        content_package: dict[str, Any],
        generated: bool,
        parent_execution_id: str,
    ) -> dict[str, Any]:
        item_id = int(item.get("id") or 0)
        callback_payload = {
            "external_event_id": _text(item.get("external_event_id")),
            "status": "generated",
            "message": {"text": content_text, "content_package": content_package},
            "action": {
                "type": "enqueue_automation_send_plan",
                "target_external_userid": external_userid,
                "sender_userid": owner_userid,
            },
        }
        configured_send_url = _text(agent.get("send_webhook_url"))
        callback_package_key = _package_key_from_send_webhook_url(configured_send_url)
        if configured_send_url and not callback_package_key:
            return self._fail(
                item_id,
                "unsupported_send_webhook_url",
                "send_webhook_url must target an AI Audience package webhook path",
                context=context,
                owner_userid=owner_userid,
                prompt_preview=prompt_preview,
            )
        callback_package_key = callback_package_key or _text(agent.get("bound_package_key"))
        if not callback_package_key:
            return self._fail(
                item_id,
                "send_webhook_url_missing",
                "send_webhook_url is required",
                context=context,
                owner_userid=owner_userid,
                prompt_preview=prompt_preview,
            )
        raw = json.dumps(callback_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        service = AudienceInboundWebhookService()
        recorded = service.handle(callback_package_key, callback_payload, raw_body=raw)
        if not bool(recorded.get("ok")):
            return {
                "ok": False,
                "item_id": item_id,
                "error": _text(recorded.get("error")) or "callback_record_failed",
                "detail": _text(recorded.get("detail") or recorded.get("error")),
            }
        inbound_event_id = _safe_int((recorded.get("recorded") or {}).get("id"))
        processed = service.process_record(inbound_event_id, parent_execution_id=parent_execution_id)
        if not bool(processed.get("ok")):
            return {
                "ok": False,
                "item_id": item_id,
                "error": _text(processed.get("error")) or "send_plan_create_failed",
                "detail": _text(processed.get("detail") or processed.get("error")),
            }
        callback_response = {"recorded": recorded, "processed": processed}
        completed = self._repo.complete_item_send_plan(
            item_id,
            owner_userid=owner_userid,
            context={**context, "external_userid": external_userid},
            prompt_preview=prompt_preview,
            raw_output=raw_output,
            content_package=content_package,
            callback_payload=callback_payload,
            callback_response=callback_response,
            generated=generated,
        )
        return {
            "ok": bool(completed.get("ok")),
            "item_id": item_id,
            "callback": callback_response,
            "deduplicated": bool(completed.get("deduplicated")),
            "real_external_call_executed": False,
        }

    def _fail(
        self,
        item_id: int,
        error_code: str,
        error_message: str,
        *,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
        owner_userid: str = "",
        prompt_preview: str = "",
    ) -> dict[str, Any]:
        return self._repo.fail_pipeline_item(
            item_id,
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
            context=context,
            owner_userid=owner_userid,
            prompt_preview=prompt_preview,
        )


__all__ = ["AutomationAgentWorker"]
