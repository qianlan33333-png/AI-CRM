from __future__ import annotations

import base64
import json
import re

import pytest

from aicrm_next.extensions.hxc.operation_cycles.markdown_renderer import normalize_chart_spec, render_markdown


def test_markdown_renderer_supports_tables_tasks_and_strikethrough() -> None:
    rendered = str(
        render_markdown(
            "# 结果\n\n| 指标 | 数值 |\n| --- | ---: |\n| 发送 | 845 |\n\n- [x] 已复盘\n\n~~旧口径~~"
        )
    )

    assert "<table>" in rendered
    assert 'type="checkbox" disabled aria-hidden="true" checked' in rendered
    assert "<s>旧口径</s>" in rendered


def test_markdown_renderer_disables_raw_html_and_unsafe_links() -> None:
    rendered = str(render_markdown('<script>alert(1)</script>\n\n[危险](javascript:alert(1))'))

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert 'href="javascript:' not in rendered


def test_chart_block_is_validated_and_encoded_without_raw_labels() -> None:
    rendered = str(
        render_markdown(
            "```chart\n"
            '{"type":"bar","title":"发送结果","labels":["<b>发送</b>"],'
            '"series":[{"name":"人数","data":[845]}]}\n'
            "```"
        )
    )
    encoded = re.search(r'data-operation-cycle-chart="([^"]+)"', rendered)

    assert encoded is not None
    assert "<b>发送</b>" not in rendered
    payload = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
    assert payload["labels"] == ["<b>发送</b>"]
    assert payload["series"][0]["data"] == [845]


def test_invalid_chart_block_fails_closed() -> None:
    rendered = str(render_markdown("```chart\n{\"type\":\"script\",\"labels\":[\"x\"],\"series\":[]}\n```"))

    assert 'data-chart-error="true"' in rendered
    assert "script" not in rendered


@pytest.mark.parametrize("chart_type", ["bar", "line", "pie", "funnel"])
def test_supported_chart_types(chart_type: str) -> None:
    spec = normalize_chart_spec(
        json.dumps(
            {
                "type": chart_type,
                "labels": ["A", "B"],
                "series": [{"name": "人数", "data": [10, 5]}],
            }
        )
    )

    assert spec["type"] == chart_type


def test_mermaid_block_is_encoded_and_never_rendered_as_raw_html() -> None:
    rendered = str(render_markdown("```mermaid\nflowchart LR\nA[开始] --> B[结束]\n```"))

    assert 'data-operation-cycle-diagram=' in rendered
    assert "A[开始] --> B[结束]" not in rendered
