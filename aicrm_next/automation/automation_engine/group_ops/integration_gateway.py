from __future__ import annotations

from typing import Any

from .material_resolver import build_group_ops_material_resolver


def resolve_group_ops_content_package_materials(
    content_package: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    return build_group_ops_material_resolver().resolve_content_package_materials(content_package)
