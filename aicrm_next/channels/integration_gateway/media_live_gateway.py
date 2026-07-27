from __future__ import annotations

from typing import Any


Json = dict[str, Any]


class MediaUploadLiveGateway:
    def upload_media_live(self, *, data_base64: str, file_name: str, content_type: str) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "media_live_gateway_disabled",
            "live_provider_upload_executed": False,
            "public_media_url_published": False,
            "token_used": False,
            "provider_secret_used": False,
        }

    def lookup_media_live(self, *, provider_reference: str) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "media_live_gateway_disabled",
            "live_provider_lookup_executed": False,
            "public_media_url_published": False,
            "token_used": False,
            "provider_secret_used": False,
        }


def build_media_upload_live_gateway() -> MediaUploadLiveGateway:
    return MediaUploadLiveGateway()
