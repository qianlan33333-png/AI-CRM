from __future__ import annotations

from typing import Any

from .repository import (
    AudienceAutomationBindingRepository,
    BindingRepositoryError,
)


class AudienceAutomationBindingService:
    def __init__(self, repository: AudienceAutomationBindingRepository | None = None) -> None:
        self._repo = repository or AudienceAutomationBindingRepository()

    @staticmethod
    def _error(exc: BindingRepositoryError) -> dict[str, Any]:
        return {"ok": False, "error": exc.code}

    def get(self, package_id: int) -> dict[str, Any]:
        try:
            exists, binding = self._repo.get_binding(int(package_id))
        except BindingRepositoryError as exc:
            return self._error(exc)
        if not exists:
            return {"ok": False, "error": "package_not_found"}
        return {"ok": True, "binding": binding}

    def put(self, package_id: int, automation_id: int, *, operator: str = "admin") -> dict[str, Any]:
        if int(automation_id or 0) <= 0:
            return {"ok": False, "error": "automation_id_required"}
        try:
            binding, deduplicated = self._repo.bind(int(package_id), int(automation_id), operator=operator)
        except BindingRepositoryError as exc:
            return self._error(exc)
        return {"ok": True, "binding": binding, "deduplicated": deduplicated}

    def put_by_agent_code(self, package_id: int, agent_code: str, *, operator: str = "admin") -> dict[str, Any]:
        if not str(agent_code or "").strip():
            return {"ok": False, "error": "agent_code_required"}
        try:
            binding, deduplicated = self._repo.bind_by_agent_code(int(package_id), agent_code, operator=operator)
        except BindingRepositoryError as exc:
            return self._error(exc)
        return {"ok": True, "binding": binding, "deduplicated": deduplicated}

    def delete(self, package_id: int, *, operator: str = "admin") -> dict[str, Any]:
        try:
            previous, deduplicated = self._repo.unbind(int(package_id), operator=operator)
        except BindingRepositoryError as exc:
            return self._error(exc)
        return {"ok": True, "binding": None, "previous_binding": previous, "deduplicated": deduplicated}

    def package_has_binding(self, package_id: int) -> bool:
        return self._repo.package_has_binding(int(package_id))

    def automation_has_binding(self, automation_id: int) -> bool:
        return self._repo.automation_has_binding(int(automation_id))

    def set_automation_status(self, automation_id: int, status: str, *, operator: str = "admin") -> dict[str, Any]:
        try:
            automation = self._repo.set_automation_status(int(automation_id), status, operator=operator)
        except BindingRepositoryError as exc:
            return self._error(exc)
        return {"ok": True, "automation": automation}


__all__ = ["AudienceAutomationBindingService"]
