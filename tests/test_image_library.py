from __future__ import annotations

import base64
from io import BytesIO

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.engagement.media_library.postgres_repo import PostgresMediaLibraryRepository
from aicrm_next.engagement.media_library.repo import normalize_tag_groups, normalize_tags, reset_media_library_fixture_state


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdac`\x00\x01\x00\x00\x07\x00\x01\xe9\x15\x08-"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-image-library-test")
    reset_media_library_fixture_state()
    return TestClient(create_app())


def assert_json_contract(payload: dict, *, ok: bool = True) -> None:
    assert payload["ok"] is ok
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["storage_adapter_mode"]
    assert payload["adapter_mode"]


def test_normalize_tags_preserves_next_media_contract() -> None:
    assert normalize_tags("review, trust ,, ") == ["review", "trust"]
    assert normalize_tags(["review", " trust", "review"]) == ["review", "trust"]
    assert normalize_tags(None) == []
    assert normalize_tags("x" * 200)[0] == "x" * 64
    assert len(normalize_tags([f"tag-{idx}" for idx in range(80)])) == 50


def test_normalize_tag_groups_preserves_dimension_boundaries() -> None:
    assert normalize_tag_groups(["主题:复盘,主题:直播", "行业:教育", "行业:教育"]) == [
        ["主题:复盘", "主题:直播"],
        ["行业:教育"],
    ]


def test_image_library_create_update_filter_facets_and_binary_routes(monkeypatch) -> None:
    client = make_client(monkeypatch)

    uploaded = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("contract.png", BytesIO(TINY_PNG), "image/png")},
        data={
            "name": "trust proof",
            "description": "one pixel proof",
            "tags": "review,trust",
            "category": "social-proof",
        },
        headers={"Idempotency-Key": "image-contract-1"},
    ).json()
    assert_json_contract(uploaded)
    assert uploaded["source_status"] == "local_upload"
    assert uploaded["side_effect_plan"]["external_storage"] == "not_executed"
    assert uploaded["side_effect_plan"]["wecom_media_upload"] == "not_executed"
    assert uploaded["item"]["tags"] == ["review", "trust"]
    image_id = uploaded["item"]["id"]

    updated = client.put(
        f"/api/admin/image-library/{image_id}",
        json={"description": "updated proof", "tags": ["review"], "category": "social-proof", "ai_metadata": {"score": 1}},
    ).json()
    assert_json_contract(updated)
    assert updated["item"]["description"] == "updated proof"
    assert updated["item"]["tags"] == ["review"]
    assert updated["item"]["category"] == "social-proof"
    assert updated["item"]["ai_metadata"] == {"score": 1}

    filtered = client.get("/api/admin/image-library?q=trust&tags=review").json()
    assert_json_contract(filtered)
    assert [item["id"] for item in filtered["items"]] == [image_id]

    unlabeled = client.get("/api/admin/image-library?only_unlabeled=true").json()
    assert_json_contract(unlabeled)
    assert image_id not in {item["id"] for item in unlabeled["items"]}

    facets = client.get("/api/admin/image-library/facets").json()
    assert_json_contract(facets)
    assert "review" in facets["tags"]

    thumbnail = client.get(f"/api/admin/image-library/{image_id}/thumbnail?size=160")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert thumbnail.headers["X-AICRM-Fallback-Used"] == "false"
    assert thumbnail.headers["X-AICRM-Real-External-Call-Executed"] == "false"

    variant = client.get(f"/api/admin/image-library/{image_id}/variants/thumb_160")
    assert variant.status_code == 200
    assert variant.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert variant.headers["X-AICRM-Real-External-Call-Executed"] == "false"


def test_image_library_dimension_groups_are_or_within_and_across(monkeypatch) -> None:
    client = make_client(monkeypatch)
    image_ids: dict[str, str] = {}
    fixtures = {
        "review-education": "主题:复盘,行业:教育",
        "live-education": "主题:直播,行业:教育",
        "review-healthcare": "主题:复盘,行业:医疗",
    }
    for key, tags in fixtures.items():
        payload = client.post(
            "/api/admin/image-library/upload",
            files={"image": (f"{key}.png", BytesIO(TINY_PNG), "image/png")},
            data={"name": key, "tags": tags},
            headers={"Idempotency-Key": f"dimension-{key}"},
        ).json()
        assert_json_contract(payload)
        image_ids[key] = payload["item"]["id"]

    grouped = client.get(
        "/api/admin/image-library",
        params=[
            ("tag_group", "主题:复盘,主题:直播"),
            ("tag_group", "行业:教育"),
        ],
    ).json()
    assert_json_contract(grouped)
    assert {item["id"] for item in grouped["items"]} == {
        image_ids["review-education"],
        image_ids["live-education"],
    }

    legacy_any = client.get(
        "/api/admin/image-library",
        params={"tags": "主题:复盘,行业:教育"},
    ).json()
    assert_json_contract(legacy_any)
    assert {image_ids[key] for key in fixtures} <= {item["id"] for item in legacy_any["items"]}


def test_postgres_image_dimension_groups_add_one_clause_per_dimension(monkeypatch) -> None:
    repo = PostgresMediaLibraryRepository("postgresql://example.invalid/db")
    captured: dict = {}

    def fake_select_list(kind, table, where, params, order_by, *, limit, offset):
        captured.update({"where": where, "params": params})
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    monkeypatch.setattr(repo, "_select_list", fake_select_list)

    repo._list_images(
        limit=80,
        offset=0,
        filters={
            "tag_groups": ["主题:复盘,主题:直播", "行业:教育"],
        },
    )

    sql = " ".join(captured["where"])
    assert sql.count("jsonb_array_elements_text(tags)") == 2
    assert captured["params"] == [["主题:复盘", "主题:直播"], ["行业:教育"]]


def test_image_library_import_routes_use_fake_adapters(monkeypatch) -> None:
    client = make_client(monkeypatch)

    from_url = client.post(
        "/api/admin/image-library/from-url",
        json={"url": "https://example.invalid/image.png", "name": "remote reference"},
        headers={"Idempotency-Key": "image-url-1"},
    ).json()
    assert_json_contract(from_url)
    assert from_url["source_status"] == "fake_import"
    assert from_url["adapter_result"]["cloud_storage"]["side_effect_executed"] is False
    assert from_url["adapter_result"]["wecom_media"]["side_effect_executed"] is False

    from_base64 = client.post(
        "/api/admin/image-library/from-base64",
        json={
            "data_base64": base64.b64encode(TINY_PNG).decode("ascii"),
            "file_name": "base64.png",
            "name": "base64 reference",
        },
    ).json()
    assert_json_contract(from_base64)
    assert from_base64["source_status"] == "fake_import"
    assert from_base64["adapter_result"]["cloud_storage"]["side_effect_executed"] is False
    assert from_base64["adapter_result"]["wecom_media"]["side_effect_executed"] is False
