from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app

ROOT = Path(__file__).resolve().parents[1]


MEDIA_LIBRARY_PAGES = (
    ("/admin/image-library", "图片素材库"),
    ("/admin/miniprogram-library", "小程序素材库"),
    ("/admin/attachment-library", "附件素材库"),
)


def _endpoint_module(path: str) -> str:
    app = create_app()
    for route in app.routes:
        if getattr(route, "path", "") == path and "GET" in getattr(route, "methods", set()):
            return route.endpoint.__module__
    raise AssertionError(f"missing route for {path}")


def test_media_library_admin_pages_render_from_native_shell() -> None:
    client = TestClient(create_app())

    for path, title in MEDIA_LIBRARY_PAGES:
        response = client.get(path)

        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert "X-AICRM-Compatibility-Facade" not in response.headers
        assert title in response.text
        assert _endpoint_module(path) == "aicrm_next.media_library.admin_pages"


def test_media_library_admin_pages_removed_from_frontend_compat_inventory() -> None:
    assert not (ROOT / "aicrm_next/frontend_compat/legacy_routes.py").exists()
    for path, _title in MEDIA_LIBRARY_PAGES:
        assert _endpoint_module(path) == "aicrm_next.media_library.admin_pages"
