from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from aicrm_next.automation_engine.group_ops.material_resolver import PostgresGroupOpsMaterialResolver
from aicrm_next.integration_gateway.wecom_media_upload_client import WeComMediaUploadClientError
from aicrm_next.media_library.wecom_lease import (
    InMemoryWeComMediaLeaseRepository,
    WeComMediaLeaseError,
    WeComMediaLeaseManager,
)
from aicrm_next.wecom_media_jobs import WeComMediaUploadAdapter, enqueue_due_media_refreshes, sync_uploaded_material
from aicrm_next.platform_foundation.external_effects import WECOM_MEDIA_UPLOAD, ExternalEffectJob
from aicrm_next.platform_foundation.external_effects.execution_policy import normalize_dispatch_result
from aicrm_next.platform_foundation.external_effects.repo import InMemoryExternalEffectRepository


NOW = datetime(2026, 7, 14, 3, 0, tzinfo=timezone.utc)


class FakeMediaRepository:
    def __init__(self) -> None:
        encoded = base64.b64encode(b"durable-media-source").decode()
        self.items = {
            ("image", "1"): {"id": 1, "enabled": True, "file_name": "welcome.png", "mime_type": "image/png", "data_base64": encoded},
            ("attachment", "2"): {"id": 2, "enabled": True, "file_name": "guide.pdf", "mime_type": "application/pdf", "data_base64": encoded},
            ("miniprogram", "3"): {"id": 3, "enabled": True, "appid": "wx-app", "pagepath": "/home", "title": "Home", "thumb_image_id": 1},
            ("image", "4"): {"id": 4, "enabled": True, "file_name": "missing.png", "mime_type": "image/png", "data_base64": ""},
        }
        self.cache_calls: list[tuple] = []

    def get_item(self, kind: str, item_id: str, *, include_data: bool = False):
        item = self.items.get((kind, str(item_id)))
        return dict(item) if item else None

    def cache_image_media_id(self, item_id: str, media_id: str, expires_at: datetime) -> None:
        self.cache_calls.append(("image", item_id, media_id, expires_at))

    def cache_attachment_media_id(self, item_id: str, media_id: str, expires_at: datetime) -> None:
        self.cache_calls.append(("attachment", item_id, media_id, expires_at))

    def cache_miniprogram_thumb_media_id(self, item_id: str, media_id: str, expires_at: datetime) -> None:
        self.cache_calls.append(("miniprogram", item_id, media_id, expires_at))


class FakeUploader:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def upload_image(self, file_name: str, payload: bytes, content_type: str):
        self.calls.append(("image", file_name, payload, content_type))
        return {"media_id": f"real-image-{len(self.calls)}", "created_at": int(NOW.timestamp())}

    def upload_attachment(self, file_name: str, payload: bytes, content_type: str):
        self.calls.append(("attachment", file_name, payload, content_type))
        return {"media_id": f"real-file-{len(self.calls)}", "created_at": int(NOW.timestamp())}


def _manager(repository=None, uploader=None):
    return WeComMediaLeaseManager(
        repository or FakeMediaRepository(),
        lease_repository=InMemoryWeComMediaLeaseRepository(),
        uploader=uploader or FakeUploader(),
        corp_id="corp-1",
        now=NOW,
    )


def test_media_lease_uploads_once_and_reuses_until_refresh_window() -> None:
    media = FakeMediaRepository()
    uploader = FakeUploader()
    manager = _manager(media, uploader)

    first = manager.ensure_ready("image", 1)
    second = manager.ensure_ready("image", 1)

    assert first["media_id"] == second["media_id"] == "real-image-1"
    assert first["provider_expires_at"] == NOW + timedelta(days=3)
    assert first["refresh_after"] == NOW + timedelta(days=2, hours=12)
    assert len(uploader.calls) == 1
    assert media.cache_calls[0][:3] == ("image", "1", "real-image-1")


def test_media_lease_force_refresh_rotates_media_id() -> None:
    uploader = FakeUploader()
    manager = _manager(uploader=uploader)

    first = manager.ensure_ready("image", 1)
    second = manager.ensure_ready("image", 1, force_refresh=True)

    assert first["media_id"] == "real-image-1"
    assert second["media_id"] == "real-image-2"
    assert second["lease_version"] == 2


def test_miniprogram_reuses_image_lease_instead_of_uploading_duplicate_thumb() -> None:
    media = FakeMediaRepository()
    uploader = FakeUploader()
    leases = InMemoryWeComMediaLeaseRepository()
    manager = WeComMediaLeaseManager(media, lease_repository=leases, uploader=uploader, corp_id="corp-1", now=NOW)

    image = manager.ensure_ready("image", 1)
    mini = manager.ensure_ready("miniprogram", 3)

    assert mini["media_id"] == image["media_id"]
    assert len(uploader.calls) == 1
    assert any(call[:3] == ("miniprogram", "3", image["media_id"]) for call in media.cache_calls)


def test_missing_durable_source_is_terminal_and_never_calls_wecom() -> None:
    uploader = FakeUploader()
    manager = _manager(uploader=uploader)

    with pytest.raises(WeComMediaLeaseError) as exc_info:
        manager.ensure_ready("image", 4)

    assert exc_info.value.code == "material_source_missing"
    assert exc_info.value.retryable is False
    assert uploader.calls == []


def test_postgres_material_resolver_uses_lease_for_all_supported_material_kinds() -> None:
    media = FakeMediaRepository()
    manager = _manager(media, FakeUploader())
    resolver = PostgresGroupOpsMaterialResolver(media, lease_manager=manager, real_upload_enabled=True)

    attachments, image_ids = resolver.resolve_content_package_materials(
        {"image_library_ids": [1], "miniprogram_library_ids": [3], "attachment_library_ids": [2]}
    )

    assert image_ids == ["real-image-1"]
    assert attachments[0]["miniprogram"]["pic_media_id"] == "real-image-1"
    assert attachments[1]["file"]["media_id"] == "real-file-2"


def test_external_effect_adapter_reports_missing_source_without_false_external_call(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv("AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS", "image:4:image")
    manager = _manager()
    adapter = WeComMediaUploadAdapter(manager_factory=lambda: manager)
    job = ExternalEffectJob(
        id=1,
        effect_type=WECOM_MEDIA_UPLOAD,
        adapter_name="wecom_media_upload",
        operation="refresh_temporary_media",
        target_type="media_library_material",
        target_id="image:4:image",
        payload_json={
            "execution_scope": "allowlisted_canary",
            "material_kind": "image",
            "material_id": 4,
            "upload_kind": "image",
            "force_refresh": True,
        },
        payload_summary_json={
            "canary_authorization": {
                "actor": "pytest",
                "reason": "explicit media canary authorization",
                "authorized_at": NOW.isoformat(),
                "authorized_job_id": 1,
                "authorized_from_version": 1,
                "duplicate_risk_confirmed": False,
            }
        },
        row_version=2,
    )

    result = adapter.dispatch(job)

    assert result.status == "failed_terminal"
    assert result.error_code == "material_source_missing"
    assert result.real_external_call_executed is False


def test_external_effect_adapter_reports_manager_build_failure_before_provider_boundary(monkeypatch) -> None:
    def failing_factory():
        raise RuntimeError("local detail")

    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv("AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS", "image:1:image")
    adapter = WeComMediaUploadAdapter(manager_factory=failing_factory)
    job = ExternalEffectJob(
        id=4,
        effect_type=WECOM_MEDIA_UPLOAD,
        adapter_name="wecom_media_upload",
        operation="refresh_temporary_media",
        target_type="media_library_material",
        target_id="image:1:image",
        payload_json={
            "execution_scope": "allowlisted_canary",
            "material_kind": "image",
            "material_id": 1,
            "upload_kind": "image",
            "force_refresh": True,
        },
        payload_summary_json={
            "canary_authorization": {
                "actor": "pytest",
                "reason": "explicit media canary authorization",
                "authorized_at": NOW.isoformat(),
                "authorized_job_id": 4,
                "authorized_from_version": 1,
                "duplicate_risk_confirmed": False,
            }
        },
        row_version=2,
    )

    result = adapter.dispatch(job)

    assert result.status == "failed_retryable"
    assert result.error_code == "local_execution_error"
    assert result.real_external_call_executed is False
    assert result.provider_result_received is False
    assert "local detail" not in result.error_message


def test_external_effect_adapter_preserves_safe_media_provider_diagnostics(monkeypatch) -> None:
    class FailingUploader:
        def upload_image(self, file_name: str, payload: bytes, content_type: str):
            raise WeComMediaUploadClientError(
                "raw provider detail must not persist",
                stage="upload",
                error_code="wecom_media_upload_failed",
                payload={"errcode": 48002, "errmsg": "raw provider detail must not persist"},
            )

    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv("AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS", "image:1:image")
    adapter = WeComMediaUploadAdapter(manager_factory=lambda: _manager(uploader=FailingUploader()))
    job = ExternalEffectJob(
        id=2,
        effect_type=WECOM_MEDIA_UPLOAD,
        adapter_name="wecom_media_upload",
        operation="refresh_temporary_media",
        target_type="media_library_material",
        target_id="image:1:image",
        payload_json={
            "execution_scope": "allowlisted_canary",
            "material_kind": "image",
            "material_id": 1,
            "upload_kind": "image",
            "force_refresh": True,
        },
        payload_summary_json={
            "canary_authorization": {
                "actor": "pytest",
                "reason": "explicit media canary authorization",
                "authorized_at": NOW.isoformat(),
                "authorized_job_id": 2,
                "authorized_from_version": 1,
                "duplicate_risk_confirmed": False,
            }
        },
        row_version=2,
    )

    result = adapter.dispatch(job)

    assert result.status == "failed_terminal"
    assert result.error_code == "permission_denied"
    assert result.real_external_call_executed is True
    assert result.provider_result_received is True
    assert result.response_summary == {
        "real_external_call_executed": True,
        "wecom_media_upload_executed": True,
        "provider_result_received": True,
        "errcode": 48002,
        "errmsg_present": True,
        "provider_error_classification": "terminal",
        "provider_stage": "upload",
    }
    assert "raw provider detail" not in str(result.response_summary)
    assert "raw provider detail" not in result.error_message


def test_media_upload_transport_failure_after_boundary_normalizes_unknown(monkeypatch) -> None:
    class FailingUploader:
        def upload_image(self, file_name: str, payload: bytes, content_type: str):
            raise WeComMediaUploadClientError(
                "network detail",
                stage="upload",
                error_code="wecom_media_http_error",
            )

    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv("AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS", "image:1:image")
    adapter = WeComMediaUploadAdapter(manager_factory=lambda: _manager(uploader=FailingUploader()))
    job = ExternalEffectJob(
        id=3,
        effect_type=WECOM_MEDIA_UPLOAD,
        adapter_name="wecom_media_upload",
        operation="refresh_temporary_media",
        target_type="media_library_material",
        target_id="image:1:image",
        payload_json={
            "execution_scope": "allowlisted_canary",
            "material_kind": "image",
            "material_id": 1,
            "upload_kind": "image",
            "force_refresh": True,
        },
        payload_summary_json={
            "canary_authorization": {
                "actor": "pytest",
                "reason": "explicit media canary authorization",
                "authorized_at": NOW.isoformat(),
                "authorized_job_id": 3,
                "authorized_from_version": 1,
                "duplicate_risk_confirmed": False,
            }
        },
        row_version=2,
    )

    dispatched = adapter.dispatch(job)
    normalized = normalize_dispatch_result(job, dispatched)

    assert dispatched.status == "failed_retryable"
    assert dispatched.error_code == "network_error"
    assert dispatched.real_external_call_executed is True
    assert dispatched.provider_result_received is False
    assert normalized.status == "unknown_after_dispatch"


def test_scheduler_enqueues_only_material_references_without_persisting_source_bytes(monkeypatch) -> None:
    class FakeDueManager:
        def list_due_materials(self, *, limit: int):
            return [{"material_kind": "image", "material_id": 1, "upload_kind": "image"}]

        def metrics(self):
            return {"total_count": 1, "refresh_due_count": 1}

    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    repository = InMemoryExternalEffectRepository()
    result = enqueue_due_media_refreshes(
        manager=FakeDueManager(),
        repository=repository,
        operator="pytest",
        now=NOW,
        repair_authorized=True,
    )
    job = repository.get_job(result["items"][0]["job_id"])

    assert result["enqueued_count"] == 1
    assert job is not None
    assert job.effect_type == WECOM_MEDIA_UPLOAD
    assert job.payload_json == {
        "material_kind": "image",
        "material_id": 1,
        "upload_kind": "image",
        "force_refresh": True,
        "bypass_push_capability": True,
    }
    assert "data_base64" not in str(job.payload_json)


def test_media_upload_request_only_enqueues_and_never_dispatches_inline(monkeypatch) -> None:
    from aicrm_next import wecom_media_jobs

    repository = InMemoryExternalEffectRepository()
    monkeypatch.setattr(wecom_media_jobs, "production_data_ready", lambda: True)
    monkeypatch.setattr(wecom_media_jobs, "build_external_effect_repository", lambda: repository)
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")

    result = sync_uploaded_material(
        material_kind="image",
        material_id=1,
        upload_kind="image",
        actor="pytest",
        idempotency_key="request-only",
    )

    job = repository.get_job(result["job_id"])
    assert result["accepted"] is True
    assert result["status"] == "queued"
    assert result["lane"] == "wecom_media"
    assert result["execution_id"]
    assert result["status_url"].endswith(result["execution_id"])
    assert result["real_external_call_executed"] is False
    assert job is not None
    assert job.status == "queued"
    assert repository.list_attempts(job.id) == []
