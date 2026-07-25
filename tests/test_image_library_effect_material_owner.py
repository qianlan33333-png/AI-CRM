from __future__ import annotations

import base64

import pytest

from aicrm_next.media_library.effect_material_port import (
    EphemeralImageMaterialRequest,
    build_image_library_effect_material_port,
)


class _Result:
    def mappings(self) -> "_Result":
        return self

    def one(self) -> dict[str, int]:
        return {"id": 73}


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params: dict[str, object]) -> _Result:
        self.calls.append((" ".join(str(statement).split()), params))
        return _Result()


def _request(file_bytes: bytes = b"image-bytes") -> EphemeralImageMaterialRequest:
    return EphemeralImageMaterialRequest(
        name="Group Ops image execution-73",
        file_name="poster.png",
        content_type="image/png",
        file_bytes=file_bytes,
        description="durable source for execution-73:poster",
    )


def test_group_ops_ephemeral_image_is_written_by_media_owner_without_committing_caller_session() -> None:
    session = _Session()

    material_id = build_image_library_effect_material_port().create_group_ops_ephemeral_image_in_session(
        session,
        request=_request(),
    )

    assert material_id == 73
    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert sql.startswith("INSERT INTO image_library")
    assert params["data_base64"] == base64.b64encode(b"image-bytes").decode("ascii")
    assert params["file_size"] == len(b"image-bytes")
    assert not hasattr(session, "commit")


def test_group_ops_ephemeral_image_rejects_empty_source_bytes() -> None:
    session = _Session()

    with pytest.raises(ValueError, match="requires file_bytes"):
        build_image_library_effect_material_port().create_group_ops_ephemeral_image_in_session(
            session,
            request=_request(b""),
        )

    assert session.calls == []
