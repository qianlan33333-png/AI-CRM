from __future__ import annotations

import re
from typing import Any


_TEMPLATE_TOKEN_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
_PROMPT_LEAK_MARKERS = (
    "你将收到以下资料",
    "你的唯一任务是",
    "最终只输出",
    "不要解释",
    "不要输出 JSON",
    "系统说明",
    "可用认知依据",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def looks_like_prompt_output(final_text: str, *, role_prompt: str = "", task_prompt: str = "") -> bool:
    candidate = _text(final_text)
    if not candidate:
        return False
    if _TEMPLATE_TOKEN_RE.search(candidate):
        return True
    role = _text(role_prompt)
    task = _text(task_prompt)
    if candidate == role or candidate == task:
        return True
    if len(task) >= 120 and (task[:120] in candidate or candidate[:120] in task):
        return True
    return any(marker in candidate for marker in _PROMPT_LEAK_MARKERS)
