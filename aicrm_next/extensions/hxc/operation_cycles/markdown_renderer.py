from __future__ import annotations

import base64
import html
import json
import math
import re
from typing import Any

from markdown_it import MarkdownIt
from markupsafe import Markup


_CHART_TYPES = {"bar", "line", "pie", "funnel"}
_TASK_ITEM_PATTERN = re.compile(r"<li>\[([ xX])\]\s*")


def _short_text(value: Any, *, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ValueError("chart text is too long")
    return text


def _number(value: Any, *, non_negative: bool) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("chart values must be numbers")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("chart values must be finite")
    if non_negative and numeric < 0:
        raise ValueError("this chart type requires non-negative values")
    if abs(numeric) > 1_000_000_000_000:
        raise ValueError("chart value is outside the supported range")
    return int(numeric) if numeric.is_integer() else numeric


def normalize_chart_spec(raw: str) -> dict[str, Any]:
    """Validate the declarative chart block before it reaches browser code."""

    payload = json.loads(str(raw or ""))
    if not isinstance(payload, dict):
        raise ValueError("chart payload must be an object")
    chart_type = _short_text(payload.get("type"), maximum=20).lower()
    if chart_type not in _CHART_TYPES:
        raise ValueError("unsupported chart type")
    raw_labels = payload.get("labels")
    if not isinstance(raw_labels, list) or not raw_labels or len(raw_labels) > 40:
        raise ValueError("chart labels must contain 1 to 40 items")
    labels = [_short_text(item, maximum=80) for item in raw_labels]
    if any(not item for item in labels):
        raise ValueError("chart labels cannot be empty")

    raw_series = payload.get("series")
    if not isinstance(raw_series, list) or not raw_series or len(raw_series) > 8:
        raise ValueError("chart series must contain 1 to 8 items")
    non_negative = chart_type in {"pie", "funnel"}
    series: list[dict[str, Any]] = []
    for index, item in enumerate(raw_series):
        if not isinstance(item, dict):
            raise ValueError("each chart series must be an object")
        values = item.get("data")
        if not isinstance(values, list) or len(values) != len(labels):
            raise ValueError("each chart series must align with labels")
        series.append(
            {
                "name": _short_text(item.get("name") or f"系列 {index + 1}", maximum=80),
                "data": [_number(value, non_negative=non_negative) for value in values],
            }
        )
    if chart_type in {"pie", "funnel"} and len(series) != 1:
        raise ValueError("pie and funnel charts support exactly one series")
    return {
        "type": chart_type,
        "title": _short_text(payload.get("title"), maximum=160),
        "unit": _short_text(payload.get("unit"), maximum=24),
        "labels": labels,
        "series": series,
    }


def _encoded_payload(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(serialized.encode("utf-8")).decode("ascii")


def _render_chart_block(raw: str) -> str:
    try:
        encoded = _encoded_payload(normalize_chart_spec(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return (
            '<div class="operation-cycle-chart-error" role="note" data-chart-error="true">'
            "图表配置无法显示，请检查 chart JSON 格式。"
            "</div>"
        )
    return (
        '<div class="operation-cycle-chart" data-operation-cycle-chart="'
        + html.escape(encoded, quote=True)
        + '"><div class="operation-cycle-chart__loading">图表加载中</div></div>'
    )


def _render_mermaid_block(raw: str) -> str:
    source = str(raw or "").strip()
    if not source or len(source) > 20_000:
        return (
            '<div class="operation-cycle-chart-error" role="note" data-diagram-error="true">'
            "流程图内容为空或超过限制。"
            "</div>"
        )
    encoded = _encoded_payload({"source": source})
    return (
        '<div class="operation-cycle-diagram" data-operation-cycle-diagram="'
        + html.escape(encoded, quote=True)
        + '"><div class="operation-cycle-chart__loading">流程图加载中</div></div>'
    )


def _markdown_engine() -> MarkdownIt:
    renderer = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": False,
            "typographer": True,
        },
    ).enable(["table", "strikethrough"])
    default_fence = renderer.renderer.rules.get("fence")

    def render_fence(tokens, index, options, env) -> str:
        token = tokens[index]
        language = str(token.info or "").strip().split(maxsplit=1)[0].lower()
        if language in {"chart", "echarts"}:
            return _render_chart_block(token.content)
        if language == "mermaid":
            return _render_mermaid_block(token.content)
        if default_fence is None:
            return ""
        return default_fence(tokens, index, options, env)

    renderer.renderer.rules["fence"] = render_fence
    return renderer


_MARKDOWN = _markdown_engine()


def render_markdown(markdown: str) -> Markup:
    """Render trusted aggregate Markdown with raw HTML and scripts disabled."""

    rendered = _MARKDOWN.render(str(markdown or ""))

    def task_item(match: re.Match[str]) -> str:
        checked = match.group(1).lower() == "x"
        checked_attr = " checked" if checked else ""
        return (
            '<li class="operation-cycle-markdown__task">'
            f'<input type="checkbox" disabled aria-hidden="true"{checked_attr}> '
        )

    rendered = _TASK_ITEM_PATTERN.sub(task_item, rendered)
    return Markup(rendered)
