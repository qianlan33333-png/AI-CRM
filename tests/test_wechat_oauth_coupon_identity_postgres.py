from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.rows import dict_row

from aicrm_next.extensions.commerce.public_product import h5_wechat_pay


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _seed_published_coupon(*, slug: str) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with _connect() as conn:
        product = conn.execute(
            """
            INSERT INTO wechat_pay_products (
                product_code, name, amount_total, currency, status, enabled
            )
            VALUES (%s, %s, 10000, 'CNY', 'active', TRUE)
            RETURNING id
            """,
            (f"product-{slug}", f"OAuth coupon {slug}"),
        ).fetchone()
        coupon = conn.execute(
            """
            INSERT INTO commerce_coupons (
                tenant_id, public_slug, name, discount_amount_total, currency,
                status, total_issue_limit, per_user_issue_limit, issued_count,
                claim_starts_at, claim_ends_at, validity_mode,
                relative_validity_days, instructions
            )
            VALUES (
                'aicrm', %s, %s, 1000, 'CNY',
                'published', 10, 1, 0,
                %s, %s, 'relative_days', 2, ''
            )
            RETURNING id
            """,
            (slug, f"OAuth coupon {slug}", now - timedelta(hours=1), now + timedelta(days=1)),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO commerce_coupon_product_bindings (
                tenant_id, coupon_id, trade_product_id
            )
            VALUES ('aicrm', %s, %s)
            """,
            (int(coupon["id"]), int(product["id"])),
        )
        conn.commit()


def _start_state(client, return_url: str) -> str:
    response = client.get(
        "/api/h5/wechat-pay/oauth/start",
        params={"return_url": return_url},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def test_new_oauth_user_is_projected_then_can_claim_coupon(
    next_client,
    next_pg_schema,
    monkeypatch,
) -> None:
    del next_pg_schema
    _seed_published_coupon(slug="oauth-new-user")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-oauth-coupon-test")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "oauth-coupon-secret")
    monkeypatch.setenv("WECHAT_PAY_OAUTH_SCOPE", "snsapi_userinfo")

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            assert (app_id, app_secret, code) == (
                "wx-oauth-coupon-test",
                "oauth-coupon-secret",
                "new-user-code",
            )
            return {
                "openid": "openid-oauth-new-user",
                "unionid": "unionid-oauth-new-user",
                "access_token": "oauth-access-token",
            }

        def fetch_userinfo(self, *, access_token: str, openid: str):
            assert (access_token, openid) == ("oauth-access-token", "openid-oauth-new-user")
            return {
                "openid": openid,
                "unionid": "unionid-oauth-new-user",
                "nickname": "OAuth 新用户",
            }

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    try:
        state = _start_state(next_client, "/c/oauth-new-user")
        callback = next_client.get(
            "/api/h5/wechat-pay/oauth/callback",
            params={"state": state, "code": "new-user-code"},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/c/oauth-new-user"

        claimed = next_client.post(
            "/api/h5/coupons/oauth-new-user/claim",
            headers={
                "User-Agent": "Mozilla/5.0 MicroMessenger/8.0",
                "Idempotency-Key": "oauth-new-user-claim-key",
            },
        )
        assert claimed.status_code == 201
        assert claimed.json()["claim"]["status"] == "available"

        with _connect() as conn:
            identity = conn.execute(
                """
                SELECT unionid, primary_openid, openids_json, identity_status
                FROM crm_user_identity
                WHERE unionid = 'unionid-oauth-new-user'
                """
            ).fetchone()
            claim = conn.execute(
                """
                SELECT unionid, status
                FROM commerce_coupon_claims
                WHERE unionid = 'unionid-oauth-new-user'
                """
            ).fetchone()
        assert identity["primary_openid"] == "openid-oauth-new-user"
        assert "openid-oauth-new-user" in identity["openids_json"]
        assert identity["identity_status"] == "active"
        assert claim == {"unionid": "unionid-oauth-new-user", "status": "available"}
    finally:
        h5_wechat_pay.reset_h5_wechat_pay_oauth_client_factory()


def test_oauth_alias_conflict_is_audited_and_does_not_issue_cookie(
    next_client,
    next_pg_schema,
    monkeypatch,
) -> None:
    del next_pg_schema
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-oauth-coupon-test")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "oauth-coupon-secret")
    monkeypatch.setenv("WECHAT_PAY_OAUTH_SCOPE", "snsapi_base")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_openid, openids_json, identity_status
            )
            VALUES (
                'unionid-existing-owner',
                'openid-shared-alias',
                jsonb_build_array('openid-shared-alias'),
                'active'
            )
            """
        )
        conn.commit()

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {
                "openid": "openid-shared-alias",
                "unionid": "unionid-conflicting-user",
                "access_token": "oauth-access-token",
            }

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    try:
        state = _start_state(next_client, "/")
        callback = next_client.get(
            "/api/h5/wechat-pay/oauth/callback",
            params={"state": state, "code": "conflicting-code"},
            follow_redirects=False,
        )

        assert callback.status_code == 409
        assert callback.headers["content-type"].startswith("text/html")
        assert "当前微信身份存在冲突" in callback.text
        assert h5_wechat_pay.COOKIE_NAME not in callback.headers.get("set-cookie", "")
        with _connect() as conn:
            conflicting_identity = conn.execute(
                "SELECT 1 FROM crm_user_identity WHERE unionid = 'unionid-conflicting-user'"
            ).fetchone()
            conflict = conn.execute(
                """
                SELECT conflict_type, unionid, candidate_unionid, openid, resolution_status
                FROM crm_user_identity_conflicts
                WHERE openid = 'openid-shared-alias'
                """
            ).fetchone()
        assert conflicting_identity is None
        assert conflict == {
            "conflict_type": "wechat_oauth_alias_conflict",
            "unionid": "unionid-conflicting-user",
            "candidate_unionid": "unionid-existing-owner",
            "openid": "openid-shared-alias",
            "resolution_status": "open",
        }
    finally:
        h5_wechat_pay.reset_h5_wechat_pay_oauth_client_factory()
