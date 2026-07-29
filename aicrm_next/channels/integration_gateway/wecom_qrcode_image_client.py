from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests
from PIL import Image, UnidentifiedImageError


TRUSTED_QRCODE_IMAGE_HOST = "wework.qpic.cn"
MAX_QRCODE_IMAGE_BYTES = 5 * 1024 * 1024
MAX_QRCODE_IMAGE_PIXELS = 25_000_000
SUPPORTED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
SUPPORTED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class WeComQrImageClientError(RuntimeError):
    def __init__(self, reason: str, *, timed_out: bool = False) -> None:
        self.reason = str(reason or "qrcode_image_download_failed")
        self.timed_out = bool(timed_out)
        super().__init__(self.reason)


@dataclass(frozen=True)
class WeComQrImage:
    file_bytes: bytes
    content_type: str


def _trusted_image_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port or 443
    except ValueError as exc:
        raise WeComQrImageClientError("qrcode_image_url_invalid") from exc
    if parsed.scheme.lower() != "https":
        raise WeComQrImageClientError("qrcode_image_https_required")
    if parsed.username is not None or parsed.password is not None or parsed.fragment or port != 443:
        raise WeComQrImageClientError("qrcode_image_url_invalid")
    hostname = str(parsed.hostname or "").strip().rstrip(".").lower()
    if hostname != TRUSTED_QRCODE_IMAGE_HOST:
        raise WeComQrImageClientError("qrcode_image_host_not_allowed")
    return urlunsplit(("https", TRUSTED_QRCODE_IMAGE_HOST, parsed.path or "/", parsed.query, ""))


class WeComQrImageClient:
    def __init__(
        self,
        *,
        http_get: Callable[..., Any] | None = None,
        timeout: tuple[float, float] = (3.0, 10.0),
        max_bytes: int = MAX_QRCODE_IMAGE_BYTES,
    ) -> None:
        self._http_get = http_get or requests.get
        self._timeout = timeout
        self._max_bytes = max(1, min(int(max_bytes), MAX_QRCODE_IMAGE_BYTES))

    def download(self, url: str) -> WeComQrImage:
        trusted_url = _trusted_image_url(url)
        try:
            response = self._http_get(
                trusted_url,
                headers={"Accept": "image/jpeg,image/png,image/webp"},
                timeout=self._timeout,
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            raise WeComQrImageClientError("qrcode_image_upstream_timeout", timed_out=True) from exc
        except requests.RequestException as exc:
            raise WeComQrImageClientError("qrcode_image_download_failed") from exc

        try:
            try:
                status_code = int(getattr(response, "status_code", 0) or 0)
                if 300 <= status_code < 400:
                    raise WeComQrImageClientError("qrcode_image_redirect_not_allowed")
                if status_code != 200:
                    raise WeComQrImageClientError("qrcode_image_upstream_failed")
                headers = getattr(response, "headers", {}) or {}
                content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type not in SUPPORTED_CONTENT_TYPES:
                    raise WeComQrImageClientError("qrcode_image_content_type_invalid")
                try:
                    content_length = int(headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > self._max_bytes:
                    raise WeComQrImageClientError("qrcode_image_too_large")
                chunks: list[bytes] = []
                total = 0
                for raw_chunk in response.iter_content(chunk_size=64 * 1024):
                    chunk = bytes(raw_chunk or b"")
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self._max_bytes:
                        raise WeComQrImageClientError("qrcode_image_too_large")
                    chunks.append(chunk)
                payload = b"".join(chunks)
            except requests.Timeout as exc:
                raise WeComQrImageClientError("qrcode_image_upstream_timeout", timed_out=True) from exc
            except requests.RequestException as exc:
                raise WeComQrImageClientError("qrcode_image_download_failed") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if not payload:
            raise WeComQrImageClientError("qrcode_image_empty")
        try:
            from io import BytesIO

            with Image.open(BytesIO(payload)) as image:
                if image.format not in SUPPORTED_IMAGE_FORMATS:
                    raise WeComQrImageClientError("qrcode_image_format_invalid")
                if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_QRCODE_IMAGE_PIXELS:
                    raise WeComQrImageClientError("qrcode_image_dimensions_invalid")
                image.verify()
        except WeComQrImageClientError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise WeComQrImageClientError("qrcode_image_payload_invalid") from exc
        return WeComQrImage(file_bytes=payload, content_type=content_type)


def build_wecom_qrcode_image_client() -> WeComQrImageClient:
    return WeComQrImageClient()
