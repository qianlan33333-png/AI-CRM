from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ApplicationError
from .runtime import fixture_mode, production_data_ready
from .runtime_settings import startup_environment_setting


class RepositoryProviderError(ApplicationError):
    status_code = 503


FIXTURE_REPOSITORY_MARKERS = ("InMemory", "Fixture", "LocalContract")


@dataclass(frozen=True)
class RepositoryProviderDecision:
    capability_owner: str
    repository_class: str
    repository_kind: str
    fixture_allowed: bool
    production_data_ready: bool
    allow_fixture_repo_in_prod: bool
    ok: bool
    error_code: str = ""
    message: str = ""


def allow_fixture_repo_in_prod() -> bool:
    value = startup_environment_setting(
        "AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD"
    ).strip().lower()
    return value in {"1", "true", "yes", "on"}


def fixture_repositories_allowed() -> bool:
    if fixture_mode():
        return True
    if production_data_ready() and allow_fixture_repo_in_prod():
        return True
    return False


def repository_class_name(repository: object) -> str:
    return repository.__class__.__name__


def repository_kind(repository: object) -> str:
    name = repository_class_name(repository)
    if any(marker in name for marker in FIXTURE_REPOSITORY_MARKERS):
        return "fixture"
    return "production"


def evaluate_repository(repository: object, *, capability_owner: str) -> RepositoryProviderDecision:
    name = repository_class_name(repository)
    kind = repository_kind(repository)
    prod_ready = production_data_ready()
    allow_flag = allow_fixture_repo_in_prod()
    allowed = kind != "fixture" or fixture_mode() or (prod_ready and allow_flag)
    error_code = ""
    message = ""
    if not allowed:
        error_code = "fixture_repository_blocked_in_production"
        message = (
            f"{capability_owner} repository provider blocked {name}; "
            "production_data_ready=true must use production/postgres repository data."
        )
    return RepositoryProviderDecision(
        capability_owner=capability_owner,
        repository_class=name,
        repository_kind=kind,
        fixture_allowed=fixture_repositories_allowed(),
        production_data_ready=prod_ready,
        allow_fixture_repo_in_prod=allow_flag,
        ok=allowed,
        error_code=error_code,
        message=message,
    )


def assert_repository_allowed(repository: object, *, capability_owner: str) -> Any:
    decision = evaluate_repository(repository, capability_owner=capability_owner)
    if not decision.ok:
        raise RepositoryProviderError(decision.message)
    return repository


def blocked_production_payload(*, capability_owner: str, detail: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "degraded": True,
        "source_status": "production_unavailable",
        "error_code": "production_repository_unavailable",
        "capability_owner": capability_owner,
        "page_error": detail or f"{capability_owner} production repository is unavailable.",
        "diagnostics": {
            "production_data_ready": production_data_ready(),
            "fixture_mode": fixture_mode(),
            "allow_fixture_repo_in_prod": allow_fixture_repo_in_prod(),
        },
    }
