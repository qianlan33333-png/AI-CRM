from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RELEASE_SHA_FILE = REPO_ROOT / ".release-sha"
UNKNOWN_RELEASE_SHA = "unknown"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _is_known(value: str) -> bool:
    return bool(value) and value.lower() not in {"unknown", "none", "null", "undefined"}


def _release_sha_file() -> Path:
    configured = _clean(os.getenv("AICRM_NEXT_RELEASE_SHA_FILE"))
    return Path(configured) if configured else DEFAULT_RELEASE_SHA_FILE


def _read_release_marker() -> str:
    try:
        return _clean(_release_sha_file().read_text(encoding="utf-8").splitlines()[0])
    except (IndexError, OSError):
        return ""


def _read_git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return _clean(result.stdout)


@lru_cache(maxsize=1)
def current_release_sha() -> str:
    """Return the runtime release sha from a deploy marker, git HEAD, or env.

    The marker/git precedence intentionally prevents stale process env values from
    outliving a production deploy. Packaged non-git runtimes can still use env.
    """

    for candidate in (
        _read_release_marker(),
        _read_git_head(),
        os.getenv("AICRM_NEXT_RELEASE_SHA"),
        os.getenv("RELEASE_SHA"),
        os.getenv("GIT_SHA"),
    ):
        value = _clean(candidate)
        if _is_known(value):
            return value
    return UNKNOWN_RELEASE_SHA


def reset_release_sha_cache() -> None:
    current_release_sha.cache_clear()
