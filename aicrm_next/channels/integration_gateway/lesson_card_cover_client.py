from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import requests


LESSON_CARD_BASE_URL = "https://ip.lhbl.com.cn/api/share/lesson-card"
MAX_LESSON_CARD_BYTES = 10 * 1024 * 1024


class LessonCardCoverClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class LessonCardCover:
    file_name: str
    content_type: str
    file_bytes: bytes


class LessonCardCoverClient:
    def __init__(self, *, http_get: Callable[..., Any] | None = None, timeout: int = 20) -> None:
        self._http_get = http_get or requests.get
        self._timeout = max(1, min(int(timeout or 20), 60))

    def download(self, lesson_id: str) -> LessonCardCover:
        normalized_id = str(lesson_id or "").strip()
        if not normalized_id:
            raise LessonCardCoverClientError("lesson_id_required")
        url = f"{LESSON_CARD_BASE_URL}/{normalized_id}.png"
        try:
            response = self._http_get(url, timeout=self._timeout)
            response.raise_for_status()
        except Exception as exc:
            raise LessonCardCoverClientError("lesson_card_cover_download_failed") from exc
        content = bytes(response.content or b"")
        if not content:
            raise LessonCardCoverClientError("lesson_card_cover_empty")
        if len(content) > MAX_LESSON_CARD_BYTES:
            raise LessonCardCoverClientError("lesson_card_cover_too_large")
        return LessonCardCover(
            file_name=f"lesson-{normalized_id}.png",
            content_type="image/png",
            file_bytes=content,
        )


def build_lesson_card_cover_client() -> LessonCardCoverClient:
    return LessonCardCoverClient()
