from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPLOAD_CLIENT = ROOT / "aicrm_next" / "app" / "admin_console" / "static" / "admin_console" / "image_upload_client.js"
IMAGE_PICKER = ROOT / "aicrm_next" / "app" / "admin_console" / "static" / "admin_console" / "image_picker.js"
MINIPROGRAM_TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "miniprogram_library.html"


def test_image_upload_client_handles_non_json_413():
    source = UPLOAD_CLIENT.read_text(encoding="utf-8")

    assert "DIRECT_UPLOAD_BYTES = 900 * 1024" in source
    assert "response.status === 413" in source
    assert "服务返回了非 JSON" in source
    assert "prepareImageForUpload" in source
    assert "DEFAULT_REQUEST_TIMEOUT_MS = 15000" in source
    assert "requestJsonWithTimeout" in source
    assert "请求超时，请检查网络或稍后重试" in source
    assert "controller.abort()" in source


def test_image_upload_client_compresses_before_post():
    source = UPLOAD_CLIENT.read_text(encoding="utf-8")

    assert "canvas.toBlob" in source
    assert "image/jpeg" in source
    assert "compressed: true" in source
    assert "MAX_SOURCE_BYTES = 2 * 1024 * 1024" in source
    assert "只能上传 JPG/PNG 图片" in source
    assert "图片大小不能超过 2MB" in source


def test_image_picker_uses_shared_upload_client():
    source = IMAGE_PICKER.read_text(encoding="utf-8")

    assert "ImageUploadClient.requestJson" in source
    assert "ImageUploadClient.prepareImageForUpload" in source
    assert "resp.json()" not in source


def test_image_picker_uses_admin_thumbnail_fallback_without_detail_base64_fetch():
    source = IMAGE_PICKER.read_text(encoding="utf-8")

    assert "function thumbnailUrl" in source
    assert "/api/admin/image-library/' + encodeURIComponent(String(item.id)) + '/thumbnail?size='" in source
    assert "item.thumb_160_url || item.thumb_url || item.thumb_320_url || fallbackThumbnailUrl" in source
    assert "/api/admin/image-library/' + item.id" not in source
    assert "data_base64" not in source


def test_image_picker_lazy_images_error_fallback_and_string_ids():
    source = IMAGE_PICKER.read_text(encoding="utf-8")

    assert "img.loading = 'lazy'" in source
    assert "img.decoding = 'async'" in source
    assert "img.setAttribute('srcset'" in source
    assert "img.setAttribute('sizes'" in source
    assert "img.onerror" in source
    assert "cell.textContent = '无图'" in source
    assert "new Set(existing.map(String))" in source
    assert "String(x.id) === String(id)" in source
    assert "String(other.dataset.id) === chosen" in source


def test_image_picker_consumers_load_upload_client_first():
    source = MINIPROGRAM_TEMPLATE.read_text(encoding="utf-8")
    assert source.index("image_upload_client.js") < source.index("image_picker.js")
