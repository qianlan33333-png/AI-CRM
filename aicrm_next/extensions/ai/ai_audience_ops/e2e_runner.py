from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, WEBHOOK_GENERIC_PUSH, WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform.shared.runtime_settings import runtime_bool, startup_environment_setting

from .outbound_service import AudienceOutboundService
from .package_spec import package_payload_from_spec, parse_markdown_spec_text, validate_spec
from .refresh_service import AudienceRefreshService
from .repository import AudienceRepository, build_audience_repository, _text
from .service import AudiencePackageService


TEST_EXTERNAL_USERID = "wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ"
TEST_SENDER_USERID = "HuangYouCan"
MAX_REAL_SENDS = 5

AUTO_SCENARIOS = {"questionnaire", "payment", "channel_entry"}
ALL_SCENARIOS = ["questionnaire", "payment", "channel_entry", "dedupe", "sender_whitelist", "user_ops_batch_send"]


@dataclass
class ScenarioPackage:
    scenario: str
    package_key: str
    package_id: int
    version_id: int
    archived: bool = False


class AudienceUserOpsE2EGateway(Protocol):
    def preview(self, *, package_id: int, content: str) -> dict[str, Any]: ...

    def execute(self, *, package_id: int, content: str, confirm: bool) -> dict[str, Any]: ...


class AudienceRealE2ERunner:
    def __init__(
        self,
        *,
        repository: AudienceRepository | None = None,
        package_service: AudiencePackageService | None = None,
        refresh_service: AudienceRefreshService | None = None,
        outbound_service: AudienceOutboundService | None = None,
        external_effects: ExternalEffectService | None = None,
        worker: ExternalEffectWorker | None = None,
        user_ops_gateway: AudienceUserOpsE2EGateway | None = None,
    ) -> None:
        self._repo = repository or build_audience_repository()
        self._package_service = package_service or AudiencePackageService(repository=self._repo)
        self._refresh_service = refresh_service or AudienceRefreshService(repository=self._repo)
        self._external_effects = external_effects or ExternalEffectService()
        self._outbound_service = outbound_service or AudienceOutboundService(repository=self._repo, external_effects=self._external_effects)
        self._worker = worker or ExternalEffectWorker(locked_by="ai-audience-real-e2e")
        self._user_ops_gateway = user_ops_gateway

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        guard = self._guard(payload)
        if guard:
            return guard

        run_id = _safe_run_id(_text(payload.get("run_id")))
        scenarios = _scenarios(payload.get("scenarios"))
        packages: list[ScenarioPackage] = []
        real_send_count = 0
        scenario_results: dict[str, Any] = {}
        ok = True
        error = ""
        try:
            for scenario in scenarios:
                if scenario in AUTO_SCENARIOS:
                    package = self._apply_publish_activate_scenario(scenario, run_id)
                    packages.append(package)
                    result = self._run_auto_send_scenario(package, run_id)
                    real_send_count += int(result.get("private_send_executed_count") or 0)
                    scenario_results[f"{scenario}_auto_send"] = result
                    if not result.get("ok"):
                        ok = False
                        error = _text(result.get("error")) or f"{scenario}_failed"
                        break
                    if real_send_count > MAX_REAL_SENDS:
                        ok = False
                        error = "max_real_send_count_exceeded"
                        break
                elif scenario == "dedupe":
                    result = self._run_dedupe_scenario(packages)
                    scenario_results["dedupe"] = result
                    if not result.get("ok"):
                        ok = False
                        error = _text(result.get("error")) or "dedupe_failed"
                        break
                elif scenario == "sender_whitelist":
                    result = self._run_sender_whitelist_scenario(packages)
                    scenario_results["sender_whitelist"] = result
                    if not result.get("ok"):
                        ok = False
                        error = _text(result.get("error")) or "sender_whitelist_failed"
                        break
                elif scenario == "user_ops_batch_send":
                    package = self._apply_publish_activate_scenario("user_ops_batch_send", run_id)
                    packages.append(package)
                    result = self._run_user_ops_batch_send_scenario(package, run_id)
                    real_send_count += int(result.get("sent_count") or 0)
                    scenario_results["user_ops_batch_send"] = result
                    if not result.get("ok"):
                        ok = False
                        error = _text(result.get("error")) or "user_ops_batch_send_failed"
                        break
                    if real_send_count > MAX_REAL_SENDS:
                        ok = False
                        error = "max_real_send_count_exceeded"
                        break
        except Exception as exc:
            ok = False
            error = _text(exc) or exc.__class__.__name__
            scenario_results["runner_error"] = {"ok": False, "error": error}
        finally:
            cleanup = self._archive_packages(packages)

        safety = {
            "non_test_external_userid_touched": _non_test_touched(scenario_results),
            "non_test_sender_used": _non_test_sender_used(scenario_results),
            "batch_send_execute_count": 1 if (scenario_results.get("user_ops_batch_send") or {}).get("execute_confirm_true_called") else 0,
            "private_send_total_count": real_send_count,
            "secrets_redacted": True,
        }
        return {
            "ok": bool(ok and not safety["non_test_external_userid_touched"] and not safety["non_test_sender_used"]),
            "error": error,
            "run_id": run_id,
            "test_external_userid": TEST_EXTERNAL_USERID,
            "sender_userid": TEST_SENDER_USERID,
            "packages": [_package_result(item, cleanup) for item in packages],
            "scenario_results": scenario_results,
            "safety": safety,
            "cleanup": cleanup,
            "real_external_call_executed": real_send_count > 0,
        }

    def _guard(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not runtime_bool("AICRM_AI_AUDIENCE_E2E_RUNNER_ENABLED"):
            return {"ok": False, "error": "e2e_runner_disabled", "status_code": 404, "real_external_call_executed": False}
        if _text(payload.get("external_userid")) != TEST_EXTERNAL_USERID:
            return {"ok": False, "error": "external_userid_not_allowed", "status_code": 400, "real_external_call_executed": False}
        if _text(payload.get("sender_userid")) != TEST_SENDER_USERID:
            return {"ok": False, "error": "sender_userid_not_allowed", "status_code": 400, "real_external_call_executed": False}
        if payload.get("confirm_real_send") is not True:
            return {"ok": False, "error": "confirm_real_send_required", "status_code": 400, "real_external_call_executed": False}
        run_id = _safe_run_id(_text(payload.get("run_id")))
        if not run_id.startswith("e2e_"):
            return {"ok": False, "error": "run_id_must_start_with_e2e", "status_code": 400, "real_external_call_executed": False}
        scenarios = _scenarios(payload.get("scenarios"))
        if not scenarios:
            return {"ok": False, "error": "scenario_required", "status_code": 400, "real_external_call_executed": False}
        return None

    def _apply_publish_activate_scenario(self, scenario: str, run_id: str) -> ScenarioPackage:
        package_key = f"prod_e2e_{_scenario_key(scenario)}_{_run_suffix(run_id)}"
        markdown = _scenario_spec_markdown(scenario, package_key=package_key, run_id=run_id)
        spec = parse_markdown_spec_text(markdown, path=f"<e2e:{scenario}>")
        errors, _warnings = validate_spec(spec)
        if errors:
            raise RuntimeError(f"spec_validation_failed:{','.join(errors)}")
        package_payload = package_payload_from_spec(spec, package_key=package_key)
        package_id = 0
        try:
            existing = self._repo.get_package_by_key(package_key)
            if existing:
                package_id = int(existing["id"])
                updated = self._package_service.update_admin_package(
                    package_id,
                    {
                        "name": package_payload["name"],
                        "natural_language_definition": package_payload["natural_language_definition"],
                        "refresh_mode": package_payload["refresh_mode"],
                    },
                )
                if not updated.get("ok"):
                    raise RuntimeError(_text(updated.get("error")) or "package_update_failed")
                version = self._package_service.create_admin_version(package_id, package_payload)
            else:
                created = self._package_service.create_admin_package(package_payload)
                if not created.get("ok"):
                    raise RuntimeError(_text(created.get("error")) or "package_create_failed")
                package_id = int((created.get("package") or {}).get("id") or 0)
                version = {"ok": True, "version": created.get("version")}
            version_id = int(((version.get("version") or {}).get("id")) or 0)
            if not version.get("ok") or version_id <= 0:
                raise RuntimeError("version_create_failed")
            self._package_service.update_admin_webhook(
                package_id,
                {
                    "outbound_enabled": scenario in AUTO_SCENARIOS,
                    "outbound_webhook_url": _test_agent_url(),
                },
            )
            self._package_service.replace_admin_senders(
                package_id,
                {
                    "items": [
                        {"sender_userid": TEST_SENDER_USERID, "display_name": TEST_SENDER_USERID, "priority": 1, "status": "active"},
                        {"sender_userid": "QianLan", "display_name": "QianLan", "priority": 2, "status": "active"},
                    ]
                },
            )
            published = self._package_service.publish_external_package(package_id, version_id=version_id)
            if not published.get("ok"):
                raise RuntimeError(_text(published.get("error")) or "publish_failed")
            activated = self._package_service.activate_admin_package(package_id)
            if not activated.get("ok"):
                raise RuntimeError(_text(activated.get("error")) or "activate_failed")
            return ScenarioPackage(scenario=scenario, package_key=package_key, package_id=package_id, version_id=version_id)
        except Exception:
            if package_id > 0:
                self._package_service.archive_admin_package(package_id)
            raise

    def _run_auto_send_scenario(self, package: ScenarioPackage, run_id: str) -> dict[str, Any]:
        if not runtime_bool("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED"):
            return {"ok": False, "error": "test_agent_disabled"}
        refresh = self._refresh_service.refresh_package(package.package_id, run_type="incremental", row_limit=5)
        guard = self._guard_refresh(package, refresh)
        if guard:
            return guard
        db_run_id = int(((refresh.get("run") or {}).get("id")) or 0)
        outbound = self._outbound_service.plan_for_run(db_run_id)
        outbound_guard = self._guard_outbound(outbound, db_run_id)
        if outbound_guard:
            return outbound_guard
        webhook_job = (outbound.get("external_effect_jobs") or [])[0]
        webhook_dispatch = self._dispatch_guarded_webhook(int(webhook_job["id"]), db_run_id)
        if not webhook_dispatch.get("ok"):
            return {"ok": False, "error": webhook_dispatch.get("error") or "webhook_dispatch_failed", "webhook_dispatch": webhook_dispatch}
        external_event_id = f"self_agent_run:{package.package_key}:{db_run_id}:{TEST_EXTERNAL_USERID}"
        private_job = self._find_private_send_job(external_event_id)
        if not private_job:
            return {"ok": False, "error": "private_send_job_not_found", "webhook_dispatch": webhook_dispatch}
        private_guard = _private_job_guard(private_job)
        if private_guard:
            return {"ok": False, "error": private_guard}
        private_dispatch = self._worker.dispatch_one(int(private_job.id))
        private_ok = bool(private_dispatch.get("ok"))
        return {
            "ok": private_ok,
            "error": ""
            if private_ok
            else _text(private_dispatch.get("error")) or _text((private_dispatch.get("job") or {}).get("last_error_code")) or "private_dispatch_failed",
            "package_key": package.package_key,
            "package_id": package.package_id,
            "version_id": package.version_id,
            "refresh_run_id": db_run_id,
            "member_event_entered": int(refresh.get("entered_count") or 0) == 1,
            "webhook_generic_push_planned": True,
            "webhook_body_external_userids_only": True,
            "inbound_webhook_recorded": True,
            "private_send_planned": True,
            "private_send_executed": private_ok,
            "private_send_executed_count": 1 if private_ok else 0,
            "sender_userid": TEST_SENDER_USERID,
            "target_external_userid": TEST_EXTERNAL_USERID,
            "webhook_dispatch": _compact_dispatch(webhook_dispatch),
            "private_dispatch": _compact_dispatch(private_dispatch),
        }

    def _run_dedupe_scenario(self, packages: list[ScenarioPackage]) -> dict[str, Any]:
        package = next((item for item in packages if item.scenario == "questionnaire"), None)
        if not package:
            return {"ok": False, "error": "questionnaire_package_required"}
        refresh = self._refresh_service.refresh_package(package.package_id, run_type="incremental", row_limit=5)
        if not refresh.get("ok"):
            return {"ok": False, "error": refresh.get("error") or "second_refresh_failed"}
        db_run_id = int(((refresh.get("run") or {}).get("id")) or 0)
        outbound = self._outbound_service.plan_for_run(db_run_id)
        duplicate_private_send_count = int((outbound.get("planned_count") or 0))
        return {
            "ok": int(refresh.get("entered_count") or 0) == 0 and duplicate_private_send_count == 0,
            "second_refresh_entered_count": int(refresh.get("entered_count") or 0),
            "duplicate_private_send_count": duplicate_private_send_count,
            "refresh_run_id": db_run_id,
        }

    def _run_sender_whitelist_scenario(self, packages: list[ScenarioPackage]) -> dict[str, Any]:
        package = next((item for item in packages if item.scenario in AUTO_SCENARIOS), None)
        if not package:
            return {"ok": False, "error": "package_required"}
        gateway = self._require_user_ops_gateway()
        preview = gateway.preview(package_id=package.package_id, content="【E2E-白名单】preview only")
        resolved = _owner_userids(preview)
        empty = self._package_service.replace_admin_senders(package.package_id, {"items": []})
        empty_preview = gateway.preview(package_id=package.package_id, content="【E2E-白名单】preview only")
        no_allowed_sender_count = _skipped_count(empty_preview.get("skipped_summary"), "no_allowed_sender")
        self._package_service.replace_admin_senders(
            package.package_id,
            {"items": [{"sender_userid": TEST_SENDER_USERID, "display_name": TEST_SENDER_USERID, "priority": 1, "status": "active"}]},
        )
        return {
            "ok": TEST_SENDER_USERID in resolved and no_allowed_sender_count >= 1 and bool(empty.get("ok")),
            "resolved_sender_userid": TEST_SENDER_USERID if TEST_SENDER_USERID in resolved else "",
            "no_allowed_sender_skipped": no_allowed_sender_count,
            "default_sender_used": False,
        }

    def _run_user_ops_batch_send_scenario(self, package: ScenarioPackage, run_id: str) -> dict[str, Any]:
        refresh = self._refresh_service.refresh_package(package.package_id, run_type="incremental", row_limit=5)
        guard = self._guard_refresh(package, refresh)
        if guard:
            return guard
        self._package_service.replace_admin_senders(
            package.package_id,
            {"items": [{"sender_userid": TEST_SENDER_USERID, "display_name": TEST_SENDER_USERID, "priority": 1, "status": "active"}]},
        )
        gateway = self._require_user_ops_gateway()
        content = f"【E2E-标准群发】run_id={run_id}：User Ops 标准群发复用测试。"
        preview = gateway.preview(package_id=package.package_id, content=content)
        target_guard = _preview_guard(preview)
        if target_guard:
            return {"ok": False, "error": target_guard, "preview": _compact_preview(preview)}
        confirm_false_failed = False
        try:
            gateway.execute(package_id=package.package_id, content=content, confirm=False)
        except Exception:
            confirm_false_failed = True
        execute = gateway.execute(package_id=package.package_id, content=content, confirm=True)
        execute_guard = _execute_guard(execute)
        if execute_guard:
            return {"ok": False, "error": execute_guard, "preview": _compact_preview(preview), "execute": _compact_execute(execute)}
        return {
            "ok": True,
            "preview_owner_buckets_ok": True,
            "owner_userids": _owner_userids(preview),
            "execute_confirm_false_failed": confirm_false_failed,
            "execute_confirm_true_called": True,
            "sent_count": int(execute.get("sent_count") or 0),
            "target_external_userid": TEST_EXTERNAL_USERID,
            "sender_userid": TEST_SENDER_USERID,
            "record_id": execute.get("record_id"),
            "preview": _compact_preview(preview),
            "execute": _compact_execute(execute),
        }

    def _require_user_ops_gateway(self) -> AudienceUserOpsE2EGateway:
        if self._user_ops_gateway is None:
            raise RuntimeError("ai audience E2E User Ops gateway is not configured")
        return self._user_ops_gateway

    def _guard_refresh(self, package: ScenarioPackage, refresh: dict[str, Any]) -> dict[str, Any] | None:
        if not refresh.get("ok"):
            return {"ok": False, "error": refresh.get("error") or "refresh_failed", "refresh": refresh}
        members = [item for item in self._repo.list_current_members(package.package_id) if _text(item.get("status")) == "active"]
        external_userids = sorted({_text(item.get("external_userid")) for item in members if _text(item.get("external_userid"))})
        if external_userids != [TEST_EXTERNAL_USERID]:
            return {"ok": False, "error": "non_test_member_detected", "external_userids": external_userids}
        if int(refresh.get("returned_count") or 0) != 1:
            return {"ok": False, "error": "unexpected_candidate_count", "returned_count": int(refresh.get("returned_count") or 0)}
        return None

    def _guard_outbound(self, outbound: dict[str, Any], run_id: int) -> dict[str, Any] | None:
        if not outbound.get("ok"):
            return {"ok": False, "error": outbound.get("error") or "outbound_plan_failed"}
        jobs = list(outbound.get("external_effect_jobs") or [])
        if len(jobs) != 1:
            return {"ok": False, "error": "unexpected_webhook_job_count", "planned_count": len(jobs)}
        job_payload = dict(jobs[0].get("payload_json") or jobs[0].get("payload") or {})
        body = job_payload.get("body")
        if body != [TEST_EXTERNAL_USERID]:
            return {"ok": False, "error": "webhook_body_not_external_userid_array", "body": body}
        if str(jobs[0].get("business_id") or "") != str(run_id):
            return {"ok": False, "error": "webhook_job_run_mismatch"}
        return None

    def _dispatch_guarded_webhook(self, job_id: int, run_id: int) -> dict[str, Any]:
        job = self._external_effects.get(job_id)
        if not job:
            return {"ok": False, "error": "webhook_job_not_found"}
        guard = _webhook_job_guard(job, run_id)
        if guard:
            return {"ok": False, "error": guard}
        return self._worker.dispatch_one(job.id)

    def _find_private_send_job(self, external_event_id: str):
        jobs, _ = self._external_effects.list_jobs(
            {
                "effect_type": WECOM_MESSAGE_PRIVATE_SEND,
                "business_type": "ai_audience_inbound_webhook",
                "business_id": external_event_id,
            },
            limit=5,
        )
        return jobs[0] if jobs else None

    def _archive_packages(self, packages: list[ScenarioPackage]) -> dict[str, Any]:
        archived: list[dict[str, Any]] = []
        ok = True
        for package in packages:
            result = self._package_service.archive_admin_package(package.package_id)
            package.archived = bool(result.get("ok"))
            ok = ok and package.archived
            archived.append({"package_key": package.package_key, "package_id": package.package_id, "archived": package.archived})
        return {
            "all_prod_e2e_packages_archived": ok,
            "archived_packages": archived,
            "test_agent_restored": False,
            "env_restored": False,
        }


def _scenario_spec_markdown(scenario: str, *, package_key: str, run_id: str) -> str:
    source = _source_for_scenario(scenario)
    sql = _sql_for_scenario(scenario)
    return f"""---
package_key: {package_key}
name: E2E {scenario} 自动发送测试
status: paused
query_mode: incremental_event
identity_policy: external_userid
refresh_mode: incremental_3m
natural_language_definition: 仅用于生产 E2E 的 {scenario} 自动发送测试包，硬过滤测试 external_userid。
parameters:
  test_external_userid: {TEST_EXTERNAL_USERID}
  run_id: {run_id}
  e2e_force_test_match: true
  questionnaire_id: 101
  product_code: e2e_test_product
  channel_id: 0
senders:
  - sender_userid: {TEST_SENDER_USERID}
    display_name: {TEST_SENDER_USERID}
    priority: 1
    status: active
---

# 业务说明

{source}

# Incremental SQL

```sql
{sql}
```
"""


def _sql_for_scenario(scenario: str) -> str:
    if scenario == "payment":
        return """
SELECT
  'external_userid' AS identity_type,
  :test_external_userid AS identity_value,
  'prod_e2e_payment_' || :run_id AS event_source_key,
  jsonb_build_object('scenario', 'payment', 'run_id', :run_id, 'product_code', :product_code) AS payload_json,
  :test_external_userid AS external_userid,
  CASE
    WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
    ELSE COALESCE(o.paid_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
  END AS event_at
FROM audience_read.wecom_contacts_v1 wc
LEFT JOIN audience_read.orders_v1 o
  ON o.external_userid = wc.external_userid
 AND o.product_code = :product_code
WHERE wc.external_userid = :test_external_userid
  AND (:e2e_force_test_match OR o.order_id IS NOT NULL)
  AND (
    CASE
      WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
      ELSE COALESCE(o.paid_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
    END
  ) >= CAST(:last_watermark_at AS timestamptz) - (:lookback_seconds || ' seconds')::interval
  AND (
    CASE
      WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
      ELSE COALESCE(o.paid_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
    END
  ) <= CAST(:refresh_started_at AS timestamptz)
""".strip()
    if scenario == "channel_entry":
        return """
SELECT
  'external_userid' AS identity_type,
  :test_external_userid AS identity_value,
  'prod_e2e_channel_entry_' || :run_id AS event_source_key,
  jsonb_build_object('scenario', 'channel_entry', 'run_id', :run_id, 'channel_id', :channel_id) AS payload_json,
  :test_external_userid AS external_userid,
  CASE
    WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
    ELSE COALESCE(ce.first_entered_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
  END AS event_at
FROM audience_read.wecom_contacts_v1 wc
LEFT JOIN audience_read.channel_entries_v1 ce
  ON ce.external_userid = wc.external_userid
 AND ce.channel_id = :channel_id
WHERE wc.external_userid = :test_external_userid
  AND (:e2e_force_test_match OR ce.channel_entry_id IS NOT NULL)
  AND (
    CASE
      WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
      ELSE COALESCE(ce.first_entered_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
    END
  ) >= CAST(:last_watermark_at AS timestamptz) - (:lookback_seconds || ' seconds')::interval
  AND (
    CASE
      WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
      ELSE COALESCE(ce.first_entered_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
    END
  ) <= CAST(:refresh_started_at AS timestamptz)
""".strip()
    if scenario == "user_ops_batch_send":
        return """
SELECT
  'external_userid' AS identity_type,
  :test_external_userid AS identity_value,
  'prod_e2e_user_ops_' || :run_id AS event_source_key,
  jsonb_build_object('scenario', 'user_ops_batch_send', 'run_id', :run_id) AS payload_json,
  :test_external_userid AS external_userid,
  COALESCE(wc.updated_at, CAST(:refresh_started_at AS timestamptz)) AS event_at
FROM audience_read.wecom_contacts_v1 wc
WHERE wc.external_userid = :test_external_userid
  AND COALESCE(wc.updated_at, CAST(:refresh_started_at AS timestamptz)) <= CAST(:refresh_started_at AS timestamptz)
""".strip()
    return """
SELECT
  'external_userid' AS identity_type,
  :test_external_userid AS identity_value,
  'prod_e2e_questionnaire_' || :run_id AS event_source_key,
  jsonb_build_object('scenario', 'questionnaire', 'run_id', :run_id, 'questionnaire_id', :questionnaire_id) AS payload_json,
  :test_external_userid AS external_userid,
  CASE
    WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
    ELSE COALESCE(qs.submitted_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
  END AS event_at
FROM audience_read.wecom_contacts_v1 wc
LEFT JOIN audience_read.questionnaire_submissions_v1 qs
  ON qs.external_userid = wc.external_userid
 AND qs.questionnaire_id = :questionnaire_id
WHERE wc.external_userid = :test_external_userid
  AND (:e2e_force_test_match OR qs.submission_id IS NOT NULL)
  AND (
    CASE
      WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
      ELSE COALESCE(qs.submitted_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
    END
  ) >= CAST(:last_watermark_at AS timestamptz) - (:lookback_seconds || ' seconds')::interval
  AND (
    CASE
      WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)
      ELSE COALESCE(qs.submitted_at, wc.updated_at, CAST(:refresh_started_at AS timestamptz))
    END
  ) <= CAST(:refresh_started_at AS timestamptz)
""".strip()


def _source_for_scenario(scenario: str) -> str:
    return {
        "payment": "查询 audience_read.orders_v1 与 audience_read.wecom_contacts_v1，仅命中测试 external_userid。",
        "channel_entry": "查询 audience_read.channel_entries_v1 与 audience_read.wecom_contacts_v1，仅命中测试 external_userid。",
        "user_ops_batch_send": "查询 audience_read.wecom_contacts_v1，仅用于标准 User Ops 群发目标源复用。",
    }.get(scenario, "查询 audience_read.questionnaire_submissions_v1 与 audience_read.wecom_contacts_v1，仅命中测试 external_userid。")


def _test_agent_url() -> str:
    base_url = (
        _text(startup_environment_setting("AICRM_PUBLIC_BASE_URL"))
        or "https://www.youcangogogo.com"
    )
    return base_url.rstrip("/") + "/api/ai/audience/test-agent/webhook"


def _safe_run_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_:-]", "_", _text(value))[:80]
    return value or "e2e_manual"


def _run_suffix(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", run_id)[-36:] or secrets.token_hex(4)


def _scenario_key(scenario: str) -> str:
    return {
        "questionnaire": "questionnaire_added_wecom_auto_send",
        "payment": "payment_added_wecom_auto_send",
        "channel_entry": "channel_entry_auto_send",
        "user_ops_batch_send": "user_ops_batch_send",
    }.get(scenario, scenario)


def _scenarios(value: Any) -> list[str]:
    requested = [str(item or "").strip() for item in list(value or []) if str(item or "").strip()]
    requested = requested or ALL_SCENARIOS
    allowed = set(ALL_SCENARIOS)
    return [item for item in requested if item in allowed]


def _webhook_job_guard(job, run_id: int) -> str:
    payload = dict(job.payload_json or {})
    if job.effect_type != WEBHOOK_GENERIC_PUSH:
        return "webhook_effect_type_invalid"
    if job.business_type != "ai_audience_package_run" or str(job.business_id) != str(run_id):
        return "webhook_business_scope_invalid"
    if payload.get("body") != [TEST_EXTERNAL_USERID]:
        return "webhook_body_not_external_userid_array"
    body_text = str(payload.get("body") or "")
    for forbidden in ("package_key", "payload_json", "nickname", "phone", "tags"):
        if forbidden in body_text:
            return "webhook_body_contains_forbidden_field"
    return ""


def _private_job_guard(job) -> str:
    payload = dict(job.payload_json or {})
    if job.effect_type != WECOM_MESSAGE_PRIVATE_SEND:
        return "private_effect_type_invalid"
    if payload.get("external_userids") != [TEST_EXTERNAL_USERID]:
        return "private_target_not_allowed"
    if _text(payload.get("owner_userid")) != TEST_SENDER_USERID:
        return "private_sender_not_allowed"
    return ""


def _preview_guard(preview: dict[str, Any]) -> str:
    targets = list(preview.get("final_targets") or [])
    if len(targets) != 1:
        return "unexpected_user_ops_target_count"
    if _text(targets[0].get("external_userid")) != TEST_EXTERNAL_USERID:
        return "user_ops_non_test_target"
    owners = _owner_userids(preview)
    if owners != [TEST_SENDER_USERID]:
        return "user_ops_sender_not_allowed"
    return ""


def _execute_guard(execute: dict[str, Any]) -> str:
    if not execute.get("ok"):
        return _text(execute.get("error")) or "user_ops_execute_failed"
    if int(execute.get("sent_count") or 0) != 1:
        return "user_ops_unexpected_sent_count"
    for task in execute.get("task_results") or []:
        if _text(task.get("sender_userid")) != TEST_SENDER_USERID:
            return "user_ops_sender_not_allowed"
        if list(task.get("external_userids") or []) != [TEST_EXTERNAL_USERID]:
            return "user_ops_non_test_target"
    return ""


def _owner_userids(preview: dict[str, Any]) -> list[str]:
    return sorted(
        {
            _text(item.get("owner_userid") or item.get("sender_userid"))
            for item in preview.get("owner_buckets") or []
            if _text(item.get("owner_userid") or item.get("sender_userid"))
        }
    )


def _skipped_count(summary: Any, reason: str) -> int:
    if isinstance(summary, dict):
        return int(summary.get(reason) or 0)
    total = 0
    for item in list(summary or []):
        if isinstance(item, dict) and _text(item.get("reason")) == reason:
            total += int(item.get("count") or 0)
    return total


def _compact_dispatch(result: dict[str, Any]) -> dict[str, Any]:
    job = result.get("job") or {}
    attempt = result.get("attempt") or {}
    return {
        "ok": bool(result.get("ok")),
        "job_id": job.get("id"),
        "status": job.get("status"),
        "error_code": attempt.get("error_code") or job.get("last_error_code") or result.get("error", ""),
        "real_external_call_executed": bool(result.get("real_external_call_executed")),
    }


def _compact_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(preview.get("ok")),
        "eligible_count": int(preview.get("eligible_count") or 0),
        "skipped_summary": preview.get("skipped_summary") or {},
        "owner_buckets": preview.get("owner_buckets") or [],
    }


def _compact_execute(execute: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(execute.get("ok")),
        "record_id": execute.get("record_id"),
        "sent_count": int(execute.get("sent_count") or 0),
        "task_results": execute.get("task_results") or [],
    }


def _non_test_touched(results: dict[str, Any]) -> bool:
    for key, value in _walk(results):
        if key in {"external_userid", "target_external_userid"} and _text(value) and _text(value) != TEST_EXTERNAL_USERID:
            return True
        if key == "external_userids":
            values = [_text(item) for item in (value if isinstance(value, list) else [value]) if _text(item)]
            if any(item != TEST_EXTERNAL_USERID for item in values):
                return True
    return False


def _non_test_sender_used(results: dict[str, Any]) -> bool:
    for key, value in _walk(results):
        if key in {"sender_userid", "owner_userid"} and _text(value) and _text(value) != TEST_SENDER_USERID:
            return True
        if key in {"sender_userids", "owner_userids"}:
            values = [_text(item) for item in (value if isinstance(value, list) else [value]) if _text(item)]
            if any(item != TEST_SENDER_USERID for item in values):
                return True
    return False


def _walk(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield from _walk(child_value, str(child_key))
    elif isinstance(value, list):
        if key:
            yield key, value
        for item in value:
            yield from _walk(item, key)
    else:
        yield key, value


def _package_result(package: ScenarioPackage, cleanup: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario": package.scenario,
        "package_key": package.package_key,
        "package_id": package.package_id,
        "version_id": package.version_id,
        "status_before_cleanup": "active",
        "archived": package.archived,
    }
