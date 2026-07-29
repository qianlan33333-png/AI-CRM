from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import segno
from PIL import Image

from aicrm_next.platform.shared.share_qr import jpeg_qr_data_url, safe_qr_download_filename


def test_shared_qr_is_high_resolution_rgb_jpeg_with_white_background() -> None:
    value = "https://crm.example.test/s/中文-safe"
    data_url = jpeg_qr_data_url(value)

    assert data_url.startswith("data:image/jpeg;base64,")
    payload = base64.b64decode(data_url.split(",", 1)[1])
    assert payload.startswith(b"\xff\xd8")
    with Image.open(BytesIO(payload)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.width >= 400
        assert image.height == image.width
        assert image.getpixel((0, 0)) == (255, 255, 255)
        expected = segno.make(value, error="m", micro=False)
        matrix = tuple(tuple(row) for row in expected.matrix)
        scale = 12
        border = 4
        assert image.width == (len(matrix) + border * 2) * scale
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                pixel = image.getpixel(((border + x) * scale + scale // 2, (border + y) * scale + scale // 2))
                assert (sum(pixel) < 384) is bool(dark)


def test_shared_qr_download_filename_is_chinese_safe_jpg() -> None:
    assert safe_qr_download_filename('暑期/课程:"A"') == "暑期_课程_A二维码.jpg"


def test_all_admin_share_download_names_are_jpg() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "aicrm_next/extensions/commerce/commerce/templates/wechat_products.html",
        root / "aicrm_next/extensions/commerce/service_period/templates/service_period_products.html",
        root / "aicrm_next/extensions/forms/questionnaire/templates/admin_console/questionnaires.html",
        root / "aicrm_next/extensions/radar/radar_links/templates/admin_console/radar_links.html",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "二维码.svg" not in combined
    assert combined.count("二维码.jpg") >= len(paths)
