from __future__ import annotations

from io import BytesIO

import pytest
import requests
from PIL import Image

from aicrm_next.channels.integration_gateway.wecom_qrcode_image_client import (
    MAX_QRCODE_IMAGE_BYTES,
    WeComQrImageClient,
    WeComQrImageClientError,
)


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (320, 320), "white").save(output, "PNG")
    return output.getvalue()


class _Response:
    def __init__(self, *, status=200, content_type="image/png", content=b""):
        self.status_code = status
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(content))}
        self._content = content

    def iter_content(self, chunk_size):
        for index in range(0, len(self._content), chunk_size):
            yield self._content[index : index + chunk_size]

    def close(self):
        return None


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://wework.qpic.cn/qr.png", "qrcode_image_https_required"),
        ("https://evil.example/qr.png", "qrcode_image_host_not_allowed"),
        ("https://user@wework.qpic.cn/qr.png", "qrcode_image_url_invalid"),
    ],
)
def test_client_rejects_non_trusted_urls_before_network(url, reason):
    calls = []
    client = WeComQrImageClient(http_get=lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(WeComQrImageClientError, match=reason):
        client.download(url)

    assert calls == []


def test_client_disables_redirects_and_accepts_supported_image():
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(content=_png_bytes())

    result = WeComQrImageClient(http_get=get).download("https://wework.qpic.cn/qr.png")

    assert result.file_bytes.startswith(b"\x89PNG")
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["stream"] is True
    assert calls[0][1]["timeout"]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (_Response(status=302, content=_png_bytes()), "qrcode_image_redirect_not_allowed"),
        (_Response(content_type="text/html", content=b"<html>"), "qrcode_image_content_type_invalid"),
        (_Response(content_type="image/png", content=b"not-an-image"), "qrcode_image_payload_invalid"),
        (_Response(content_type="image/png", content=b"x" * (MAX_QRCODE_IMAGE_BYTES + 1)), "qrcode_image_too_large"),
    ],
)
def test_client_rejects_redirect_oversize_and_invalid_images(response, reason):
    with pytest.raises(WeComQrImageClientError, match=reason):
        WeComQrImageClient(http_get=lambda *args, **kwargs: response).download("https://wework.qpic.cn/qr.png")


def test_client_classifies_timeout():
    def get(*args, **kwargs):
        raise requests.Timeout("slow")

    with pytest.raises(WeComQrImageClientError) as exc_info:
        WeComQrImageClient(http_get=get).download("https://wework.qpic.cn/qr.png")

    assert exc_info.value.reason == "qrcode_image_upstream_timeout"
    assert exc_info.value.timed_out is True


def test_client_classifies_streaming_timeout():
    class StreamingTimeoutResponse(_Response):
        def iter_content(self, chunk_size):
            raise requests.Timeout("stream stalled")

    with pytest.raises(WeComQrImageClientError) as exc_info:
        WeComQrImageClient(http_get=lambda *args, **kwargs: StreamingTimeoutResponse(content=_png_bytes())).download(
            "https://wework.qpic.cn/qr.png"
        )

    assert exc_info.value.reason == "qrcode_image_upstream_timeout"
    assert exc_info.value.timed_out is True
