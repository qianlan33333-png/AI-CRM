from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import text

from aicrm_next.platform.shared.automation_agent_webhook_contract import (
    automation_agent_code_from_webhook_url,
)
from aicrm_next.platform.shared.db_session import get_session_factory


_AUDIENCE_WEBHOOK_PREFIX = "/api/ai/audience/packages/"
_AUDIENCE_WEBHOOK_SUFFIX = "/webhook"
_FIRST_PARTY_HOSTS = {
    "127.0.0.1",
    "::1",
    "localhost",
    "www.youcangogogo.com",
    "id-dev.youcangogogo.com",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_url_host(value: Any) -> str:
    try:
        return _text(urlparse(_text(value)).hostname)
    except ValueError:
        return ""


def _first_party_url(value: Any) -> bool:
    raw = _text(value)
    if not raw:
        return False
    parsed = urlparse(raw)
    if not parsed.scheme and not parsed.netloc:
        return raw.startswith("/")
    return _text(parsed.scheme).lower() in {"http", "https"} and _text(parsed.hostname).lower() in _FIRST_PARTY_HOSTS


def audience_package_key_from_webhook_url(value: Any) -> str:
    raw = _text(value)
    if not raw or not _first_party_url(raw):
        return ""
    path = urlparse(raw).path
    if not path.startswith(_AUDIENCE_WEBHOOK_PREFIX) or not path.endswith(_AUDIENCE_WEBHOOK_SUFFIX):
        return ""
    package_key = unquote(path[len(_AUDIENCE_WEBHOOK_PREFIX) : -len(_AUDIENCE_WEBHOOK_SUFFIX)]).strip("/")
    return package_key if package_key and "/" not in package_key else ""


@dataclass(frozen=True)
class BindingPrecheckReport:
    issues: tuple[dict[str, Any], ...]
    bindings: tuple[dict[str, Any], ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issue_count": len(self.issues),
            "binding_count": len(self.bindings),
            "issues": [dict(item) for item in self.issues],
            "bindings": [dict(item) for item in self.bindings],
        }


def inspect_automation_bindings(connection: Any) -> BindingPrecheckReport:
    packages = {
        _text(row["package_key"]): dict(row)
        for row in connection.execute(
            text("SELECT id, package_key, name, status FROM ai_audience_package")
        ).mappings()
        if _text(row["package_key"])
    }
    agents = {
        _text(row["agent_code"]): dict(row)
        for row in connection.execute(
            text(
                """
                SELECT id, agent_code, agent_name, automation_type, bound_package_key,
                       status, send_webhook_url
                FROM automation_agent_runtime_config
                WHERE status <> 'archived'
                ORDER BY id ASC
                """
            )
        ).mappings()
        if _text(row["agent_code"])
    }
    subscriptions = [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT id, package_id, status, trigger_event_type, target_type, webhook_url
                FROM ai_audience_outbound_subscription
                ORDER BY id ASC
                """
            )
        ).mappings()
    ]
    packages_by_id = {int(row["id"]): row for row in packages.values()}
    issues: list[dict[str, Any]] = []
    pair_sources: dict[tuple[str, str], set[str]] = {}
    subscription_pairs: dict[tuple[str, str], list[int]] = {}

    def issue(kind: str, **details: Any) -> None:
        issues.append({"kind": kind, **details})

    def pair(agent_code: str, package_key: str, source: str) -> None:
        agent_code = _text(agent_code)
        package_key = _text(package_key)
        if not agent_code or not package_key:
            return
        pair_sources.setdefault((agent_code, package_key), set()).add(source)

    for agent_code, agent in agents.items():
        bound_key = _text(agent.get("bound_package_key"))
        send_url = _text(agent.get("send_webhook_url"))
        send_key = audience_package_key_from_webhook_url(send_url) if send_url else ""
        if bound_key:
            if bound_key not in packages or _text(packages[bound_key].get("status")) == "archived":
                issue("orphan_agent_binding", automation_id=int(agent["id"]), agent_code=agent_code, package_key=bound_key)
            else:
                pair(agent_code, bound_key, "bound_package_key")
        if send_url and not send_key:
            issue(
                "external_or_invalid_send_url",
                automation_id=int(agent["id"]),
                agent_code=agent_code,
                host=_safe_url_host(send_url),
            )
        elif send_key:
            if send_key not in packages or _text(packages[send_key].get("status")) == "archived":
                issue("orphan_send_url", automation_id=int(agent["id"]), agent_code=agent_code, package_key=send_key)
            else:
                pair(agent_code, send_key, "send_webhook_url")
        if bound_key and send_key and bound_key != send_key:
            issue(
                "agent_package_mismatch",
                automation_id=int(agent["id"]),
                agent_code=agent_code,
                bound_package_key=bound_key,
                send_package_key=send_key,
            )

    for subscription in subscriptions:
        subscription_id = int(subscription.get("id") or 0)
        package = packages_by_id.get(int(subscription.get("package_id") or 0))
        package_key = _text((package or {}).get("package_key"))
        url = _text(subscription.get("webhook_url"))
        agent_code = automation_agent_code_from_webhook_url(url)
        if not package or _text(package.get("status")) == "archived":
            issue("orphan_subscription_package", subscription_id=subscription_id, package_id=int(subscription.get("package_id") or 0))
            continue
        if _text(subscription.get("target_type")) != "webhook" or not url:
            issue("orphan_subscription_target", subscription_id=subscription_id, package_key=package_key)
            continue
        if not agent_code:
            issue(
                "external_or_invalid_subscription_url",
                subscription_id=subscription_id,
                package_key=package_key,
                host=_safe_url_host(url),
            )
            continue
        if _text(subscription.get("trigger_event_type")) != "entered":
            issue(
                "unsupported_subscription_trigger",
                subscription_id=subscription_id,
                package_key=package_key,
                trigger_event_type=_text(subscription.get("trigger_event_type")),
            )
            continue
        if agent_code not in agents:
            issue("orphan_subscription_agent", subscription_id=subscription_id, package_key=package_key, agent_code=agent_code)
            continue
        pair(agent_code, package_key, f"subscription:{subscription_id}")
        subscription_pairs.setdefault((agent_code, package_key), []).append(subscription_id)

    for (agent_code, package_key), ids in subscription_pairs.items():
        if len(ids) > 1:
            issue(
                "duplicate_internal_subscriptions",
                agent_code=agent_code,
                package_key=package_key,
                subscription_ids=ids,
            )

    packages_for_agent: dict[str, set[str]] = {}
    agents_for_package: dict[str, set[str]] = {}
    for agent_code, package_key in pair_sources:
        packages_for_agent.setdefault(agent_code, set()).add(package_key)
        agents_for_package.setdefault(package_key, set()).add(agent_code)
    for agent_code, package_keys in packages_for_agent.items():
        if len(package_keys) > 1:
            issue("automation_bound_to_multiple_packages", agent_code=agent_code, package_keys=sorted(package_keys))
    for package_key, agent_codes in agents_for_package.items():
        if len(agent_codes) > 1:
            issue("package_bound_to_multiple_automations", package_key=package_key, agent_codes=sorted(agent_codes))

    unique_issues: list[dict[str, Any]] = []
    seen_issues: set[str] = set()
    for item in issues:
        marker = repr(sorted(item.items()))
        if marker in seen_issues:
            continue
        seen_issues.add(marker)
        unique_issues.append(item)

    bindings = []
    if not unique_issues:
        for (agent_code, package_key), sources in sorted(pair_sources.items()):
            agent = agents[agent_code]
            package = packages[package_key]
            bindings.append(
                {
                    "automation_id": int(agent["id"]),
                    "agent_code": agent_code,
                    "automation_status": _text(agent.get("status")),
                    "package_id": int(package["id"]),
                    "package_key": package_key,
                    "subscription_ids": subscription_pairs.get((agent_code, package_key), []),
                    "sources": sorted(sources),
                }
            )
    return BindingPrecheckReport(issues=tuple(unique_issues), bindings=tuple(bindings))


def inspect_runtime_automation_bindings() -> BindingPrecheckReport:
    with get_session_factory()() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = inspect_automation_bindings(session.connection())
        session.rollback()
    return report


__all__ = [
    "BindingPrecheckReport",
    "audience_package_key_from_webhook_url",
    "inspect_automation_bindings",
    "inspect_runtime_automation_bindings",
]
