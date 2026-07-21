from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Protocol

from aicrm_next.shared.runtime import database_mode, raw_database_url

from .domain import normalize_identity_request
from .dto import ContactPoint, IdentityResolution, IdentityResolveResult, ResolvePersonIdentityRequest
from .resolver import DBAPIIdentityResolver, PostgresIdentityResolver, classify_identity_candidates, resolved_identity_or_none


class IdentityBindingRepository(Protocol):
    def bind_mobile_to_external_contact(
        self,
        *,
        external_userid: str,
        mobile: str,
        owner_userid: str = "",
        bind_by_userid: str = "",
        customer_name: str = "",
        force_rebind: bool = False,
    ) -> dict[str, Any]: ...


class FixtureIdentityRepository:
    def __init__(self) -> None:
        self._people = [
            IdentityResolution(
                person_id="person_001",
                external_userid="wx_ext_001",
                mobile="13800138000",
                openid="openid_001",
                unionid="unionid_001",
                binding_status="bound",
                owner_userid="ZhaoYanFang",
                contact_points=[
                    ContactPoint(type="wecom_external_userid", value="wx_ext_001", verified=True),
                    ContactPoint(type="mobile", value="13800138000", verified=True),
                ],
            ),
            IdentityResolution(
                person_id="person_002",
                external_userid="wx_ext_002",
                mobile=None,
                openid="openid_002",
                unionid="unionid_002",
                binding_status="unbound",
                owner_userid="LiuXiao",
                contact_points=[ContactPoint(type="wecom_external_userid", value="wx_ext_002", verified=True)],
            ),
            IdentityResolution(
                person_id="person_003",
                external_userid=None,
                mobile="13900139000",
                binding_status="mobile_only",
                owner_userid=None,
                contact_points=[ContactPoint(type="mobile", value="13900139000", verified=False)],
            ),
        ]

    def resolve_result(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult:
        query = normalize_identity_request(query)
        rows: list[dict[str, Any]] = []
        for person in self._people:
            matched_unionid = bool(query.unionid and person.unionid == query.unionid)
            matched_external_userid = bool(query.external_userid and person.external_userid == query.external_userid)
            matched_openid = bool(query.openid and person.openid == query.openid)
            matched_mobile = bool(query.mobile and person.mobile == query.mobile)
            if not any((matched_unionid, matched_external_userid, matched_openid, matched_mobile)):
                continue
            rows.append(
                {
                    "unionid": person.unionid,
                    "person_id": person.person_id,
                    "external_userid": person.external_userid,
                    "openid": person.openid,
                    "mobile": person.mobile,
                    "mobile_verified": any(point.type == "mobile" and point.verified for point in person.contact_points),
                    "owner_userid": person.owner_userid,
                    "status": "active" if person.unionid else "pending_merge",
                    "matched_unionid": matched_unionid,
                    "matched_external_userid": matched_external_userid,
                    "matched_openid": matched_openid,
                    "matched_mobile": matched_mobile,
                }
            )
        return classify_identity_candidates(query, rows)

    def resolve(self, query: ResolvePersonIdentityRequest) -> IdentityResolution | None:
        return resolved_identity_or_none(self.resolve_result(query))

    def list_external_contact_owner_userids(self, external_userid: str) -> set[str]:
        person = self.resolve(ResolvePersonIdentityRequest(external_userid=external_userid))
        if person is None:
            return set()
        return {
            owner
            for owner in {
                _text(person.owner_userid),
                _text(person.follow_user_userid),
            }
            if owner
        }


class FixtureIdentityBindingRepository:
    source_status = "fixture_identity_binding"

    def __init__(self) -> None:
        self._bindings: dict[str, dict[str, Any]] = {}

    def bind_mobile_to_external_contact(
        self,
        *,
        external_userid: str,
        mobile: str,
        owner_userid: str = "",
        bind_by_userid: str = "",
        customer_name: str = "",
        force_rebind: bool = False,
    ) -> dict[str, Any]:
        person_id = f"fixture_person_{mobile[-4:]}"
        self._bindings[external_userid] = {
            "person_id": person_id,
            "external_userid": external_userid,
            "mobile": mobile,
            "owner_userid": owner_userid,
            "bind_by_userid": bind_by_userid,
            "customer_name": customer_name,
        }
        return {
            "ok": True,
            "source_status": self.source_status,
            "external_userid": external_userid,
            "mobile": mobile,
            "person_id": person_id,
            "binding_status": "bound",
            "side_effect_executed": False,
        }


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class PostgresIdentityRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = _psycopg_url(str(database_url or raw_database_url()).strip())

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._database_url, row_factory=dict_row)

    def resolve_result(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult:
        return PostgresIdentityResolver(self._connect).resolve(query)

    def resolve(self, query: ResolvePersonIdentityRequest) -> IdentityResolution | None:
        return resolved_identity_or_none(self.resolve_result(query))

    def list_external_contact_owner_userids(self, external_userid: str) -> set[str]:
        normalized_external = _text(external_userid)
        if not normalized_external:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT owner_userid
                FROM (
                    SELECT NULLIF(user_id, '') AS owner_userid
                    FROM wecom_external_contact_follow_users
                    WHERE external_userid = %s
                      AND COALESCE(relation_status, 'active') = 'active'
                    UNION ALL
                    SELECT NULLIF(follow_user_userid, '') AS owner_userid
                    FROM wecom_external_contact_identity_map
                    WHERE external_userid = %s
                      AND COALESCE(status, 'active') = 'active'
                    UNION ALL
                    SELECT NULLIF(primary_owner_userid, '') AS owner_userid
                    FROM crm_user_identity
                    WHERE (
                            primary_external_userid = %s
                            OR jsonb_exists(external_userids_json, %s)
                            OR EXISTS (
                                SELECT 1
                                FROM jsonb_array_elements(external_userids_json) AS alias(value)
                                WHERE jsonb_typeof(alias.value) = 'object'
                                  AND alias.value ->> 'external_userid' = %s
                            )
                          )
                      AND COALESCE(identity_status, 'active') = 'active'
                ) owners
                WHERE COALESCE(owner_userid, '') <> ''
                """,
                (
                    normalized_external,
                    normalized_external,
                    normalized_external,
                    normalized_external,
                    normalized_external,
                ),
            ).fetchall()
        return {_text(row.get("owner_userid")) for row in rows if _text(row.get("owner_userid"))}

    def _from_user_identity(self, row, *, matched_by: str) -> IdentityResolution:
        external_userid = _text(row.get("external_userid"))
        unionid = _text(row.get("unionid")) or None
        openid = _text(row.get("openid")) or None
        mobile = _text(row.get("mobile")) or None
        follow_user_userid = _text(row.get("follow_user_userid"))
        contact_points = []
        if unionid:
            contact_points.append(ContactPoint(type="wechat_unionid", value=unionid, verified=True))
        if external_userid:
            contact_points.append(ContactPoint(type="wecom_external_userid", value=external_userid, verified=True))
        if openid:
            contact_points.append(ContactPoint(type="wechat_openid", value=openid, verified=True))
        if mobile:
            contact_points.append(ContactPoint(type="mobile", value=mobile, verified=True))
        return IdentityResolution(
            person_id=None,
            external_userid=external_userid or None,
            mobile=mobile,
            openid=openid,
            unionid=unionid,
            binding_status="bound" if unionid else "unresolved",
            owner_userid=_text(row.get("owner_userid")) or follow_user_userid or None,
            identity_map_id=None,
            follow_user_userid=follow_user_userid or None,
            matched_by=matched_by,
            contact_points=contact_points,
        )

    def _from_identity_map(self, row, *, matched_by: str, mobile: str = "") -> IdentityResolution:
        external_userid = _text(row.get("external_userid"))
        unionid = _text(row.get("unionid")) or None
        openid = _text(row.get("openid")) or None
        follow_user_userid = _text(row.get("follow_user_userid"))
        contact_points = []
        if external_userid:
            contact_points.append(ContactPoint(type="wecom_external_userid", value=external_userid, verified=True))
        if unionid:
            contact_points.append(ContactPoint(type="wechat_unionid", value=unionid, verified=True))
        if openid:
            contact_points.append(ContactPoint(type="wechat_openid", value=openid, verified=True))
        if mobile:
            contact_points.append(ContactPoint(type="mobile", value=mobile, verified=True))
        return IdentityResolution(
            person_id=str(row.get("person_id")) if row.get("person_id") is not None else None,
            external_userid=external_userid or None,
            mobile=mobile or _text(row.get("mobile")) or None,
            openid=openid,
            unionid=unionid,
            binding_status="bound" if external_userid else "unresolved",
            owner_userid=_text(row.get("owner_userid")) or follow_user_userid or None,
            identity_map_id=int(row["identity_map_id"]) if row.get("identity_map_id") is not None else None,
            follow_user_userid=follow_user_userid or None,
            matched_by=matched_by,
            contact_points=contact_points,
        )


class PostgresIdentityBindingRepository:
    source_status = "production_postgres_identity_binding"

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = _psycopg_url(str(database_url or raw_database_url()).strip())

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._database_url, row_factory=dict_row)

    def bind_mobile_to_external_contact(
        self,
        *,
        external_userid: str,
        mobile: str,
        owner_userid: str = "",
        bind_by_userid: str = "",
        customer_name: str = "",
        force_rebind: bool = False,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                resolution = DBAPIIdentityResolver(cur).resolve(
                    ResolvePersonIdentityRequest(external_userid=external_userid)
                )
                if resolution.status == "conflict":
                    self._record_conflict(
                        cur,
                        conflict_type="external_userid_alias_conflict",
                        external_userid=external_userid,
                        mobile=mobile,
                    )
                    conn.commit()
                    return self._blocked_result(
                        external_userid=external_userid,
                        mobile=mobile,
                        status="conflict",
                        reason=resolution.reason,
                    )
                if resolution.status != "resolved" or resolution.identity is None or not resolution.identity.unionid:
                    self._enqueue_mobile_identity_resolution(
                        cur,
                        external_userid=external_userid,
                        mobile=mobile,
                        owner_userid=owner_userid,
                        customer_name=customer_name,
                        operator=bind_by_userid,
                    )
                    conn.commit()
                    return self._blocked_result(
                        external_userid=external_userid,
                        mobile=mobile,
                        status="pending",
                        reason=resolution.reason or "identity_pending_resolution",
                    )

                unionid = _text(resolution.identity.unionid)
                canonical = cur.execute(
                    """
                    SELECT unionid, mobile_normalized, legacy_person_id, identity_status
                    FROM crm_user_identity
                    WHERE unionid = %s
                    FOR UPDATE
                    """,
                    (unionid,),
                ).fetchone()
                if not canonical or _text(canonical.get("identity_status") or "active") != "active":
                    self._record_conflict(
                        cur,
                        conflict_type="canonical_identity_not_active",
                        unionid=unionid,
                        external_userid=external_userid,
                        mobile=mobile,
                    )
                    conn.commit()
                    return self._blocked_result(
                        external_userid=external_userid,
                        mobile=mobile,
                        unionid=unionid,
                        status="conflict",
                        reason="canonical_identity_not_active",
                    )

                mobile_resolution = DBAPIIdentityResolver(cur, for_update=True).resolve(
                    ResolvePersonIdentityRequest(mobile=mobile)
                )
                mobile_unionid = (
                    _text(mobile_resolution.identity.unionid)
                    if mobile_resolution.status == "resolved" and mobile_resolution.identity is not None
                    else ""
                )
                if mobile_resolution.status in {"pending", "conflict"} or (
                    mobile_unionid and mobile_unionid != unionid
                ):
                    self._record_conflict(
                        cur,
                        conflict_type="mobile_alias_conflict",
                        unionid=unionid,
                        candidate_unionid=mobile_unionid,
                        external_userid=external_userid,
                        mobile=mobile,
                    )
                    conn.commit()
                    return self._blocked_result(
                        external_userid=external_userid,
                        mobile=mobile,
                        unionid=unionid,
                        status="conflict",
                        reason="mobile_alias_conflict",
                    )

                existing_mobile = _text(canonical.get("mobile_normalized"))
                if existing_mobile and existing_mobile != mobile and not force_rebind:
                    self._record_conflict(
                        cur,
                        conflict_type="canonical_mobile_rebind_blocked",
                        unionid=unionid,
                        external_userid=external_userid,
                        mobile=mobile,
                    )
                    conn.commit()
                    return self._blocked_result(
                        external_userid=external_userid,
                        mobile=mobile,
                        unionid=unionid,
                        status="conflict",
                        reason="canonical_mobile_rebind_blocked",
                    )

                contact = self._fetch_contact_profile(cur, external_userid=external_userid)
                resolved_owner = owner_userid or _text(contact.get("owner_userid")) if contact else owner_userid
                resolved_name = customer_name or _text(contact.get("customer_name")) if contact else customer_name
                updated = cur.execute(
                    """
                    UPDATE crm_user_identity
                    SET mobile = %s,
                        mobile_normalized = %s,
                        mobile_verified = TRUE,
                        mobile_source = 'mobile_bind',
                        primary_owner_userid = COALESCE(NULLIF(%s, ''), primary_owner_userid),
                        customer_name = COALESCE(NULLIF(%s, ''), customer_name),
                        profile_json = COALESCE(profile_json, '{}'::jsonb) || %s::jsonb,
                        last_seen_at = NOW(),
                        updated_at = NOW()
                    WHERE unionid = %s
                      AND identity_status = 'active'
                    RETURNING unionid, legacy_person_id, primary_owner_userid, customer_name
                    """,
                    (
                        mobile,
                        mobile,
                        resolved_owner,
                        resolved_name,
                        json.dumps(
                            {
                                "mobile_bind": {
                                    "source": "identity_contact",
                                    "operator_present": bool(_text(bind_by_userid)),
                                }
                            },
                            ensure_ascii=False,
                            default=_json_default,
                        ),
                        unionid,
                    ),
                ).fetchone()
                if not updated:
                    raise RuntimeError("canonical identity mobile update did not return a row")
            conn.commit()
        return {
            "ok": True,
            "source_status": self.source_status,
            "external_userid": external_userid,
            "mobile": mobile,
            "unionid": unionid,
            "person_id": _text(updated.get("legacy_person_id")),
            "owner_userid": _text(updated.get("primary_owner_userid")) or resolved_owner,
            "customer_name": _text(updated.get("customer_name")) or resolved_name,
            "binding_status": "bound",
            "side_effect_executed": True,
            "matched_by": "external_userid",
        }

    def _blocked_result(
        self,
        *,
        external_userid: str,
        mobile: str,
        status: str,
        reason: str,
        unionid: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "source_status": self.source_status,
            "external_userid": external_userid,
            "mobile": mobile,
            "unionid": unionid,
            "person_id": "",
            "binding_status": status,
            "reason": reason,
            "side_effect_executed": False,
        }

    def _fetch_contact_profile(self, cur, *, external_userid: str) -> dict[str, Any] | None:
        return cur.execute(
            """
            SELECT
                im.external_userid,
                COALESCE(NULLIF(fu.user_id, ''), NULLIF(im.follow_user_userid, '')) AS owner_userid,
                COALESCE(NULLIF(im.name, ''), '') AS customer_name,
                COALESCE(NULLIF(fu.remark, ''), '') AS remark
            FROM wecom_external_contact_identity_map im
            LEFT JOIN wecom_external_contact_follow_users fu
              ON fu.external_userid = im.external_userid
             AND COALESCE(fu.relation_status, 'active') = 'active'
            WHERE im.external_userid = %s
            ORDER BY fu.is_primary DESC NULLS LAST, fu.updated_at DESC NULLS LAST, im.updated_at DESC, im.id DESC
            LIMIT 1
            """,
            (external_userid,),
        ).fetchone()

    def _enqueue_mobile_identity_resolution(
        self,
        cur,
        *,
        external_userid: str,
        mobile: str,
        owner_userid: str,
        customer_name: str,
        operator: str,
    ) -> None:
        cur.execute(
            """
            INSERT INTO crm_user_identity_resolution_queue (
                source_type, source_key, source_table, source_id,
                external_userid, mobile, payload_json, raw_payload_json,
                reason, status, last_seen_at, updated_at
            )
            VALUES (
                'identity_contact_mobile_bind', %s, 'crm_user_identity', %s,
                %s, %s, %s::jsonb, %s::jsonb,
                'pending_unionid_for_mobile_bind', 'pending', NOW(), NOW()
            )
            ON CONFLICT (source_type, source_key)
            WHERE status = 'pending' AND source_type <> '' AND source_key <> ''
            DO UPDATE SET
                external_userid = COALESCE(NULLIF(EXCLUDED.external_userid, ''), crm_user_identity_resolution_queue.external_userid),
                mobile = COALESCE(NULLIF(EXCLUDED.mobile, ''), crm_user_identity_resolution_queue.mobile),
                payload_json = crm_user_identity_resolution_queue.payload_json || EXCLUDED.payload_json,
                raw_payload_json = crm_user_identity_resolution_queue.raw_payload_json || EXCLUDED.raw_payload_json,
                reason = EXCLUDED.reason,
                last_seen_at = NOW(),
                updated_at = NOW()
            RETURNING *
            """,
            (
                external_userid,
                external_userid,
                external_userid,
                mobile,
                json.dumps(
                    {
                        "owner_userid_present": bool(_text(owner_userid)),
                        "customer_name_present": bool(_text(customer_name)),
                    },
                    ensure_ascii=False,
                    default=_json_default,
                ),
                json.dumps(
                    {"operator_present": bool(_text(operator)), "source": "identity_contact_mobile_bind"},
                    ensure_ascii=False,
                    default=_json_default,
                ),
            ),
        )
        row = cur.fetchone()
        if row:
            from .resolution_effects import plan_identity_resolution_effect

            plan_identity_resolution_effect(
                cur,
                dict(row),
                source_route="identity_contact.mobile_bind_resolution.enqueue",
            )

    def _record_conflict(
        self,
        cur,
        *,
        conflict_type: str,
        unionid: str = "",
        candidate_unionid: str = "",
        external_userid: str = "",
        mobile: str = "",
    ) -> None:
        digest = hashlib.sha256(
            f"{conflict_type}|{unionid}|{candidate_unionid}|{external_userid}|{mobile}".encode("utf-8")
        ).hexdigest()
        cur.execute(
            """
            INSERT INTO crm_user_identity_conflicts (
                conflict_type, unionid, candidate_unionid, external_userid, mobile,
                source_type, source_key, payload_json, source_payload_json,
                status, resolution_status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                'identity_contact_mobile_bind', %s, %s::jsonb, %s::jsonb,
                'open', 'open', NOW(), NOW()
            )
            """,
            (
                conflict_type,
                unionid,
                candidate_unionid,
                external_userid,
                mobile,
                digest,
                json.dumps({"candidate_count": 1 if candidate_unionid else 0}),
                json.dumps({"source": "identity_contact_mobile_bind"}),
            ),
        )


_DEFAULT_BINDING_REPO = FixtureIdentityBindingRepository()


def build_identity_binding_repository() -> IdentityBindingRepository:
    if database_mode() == "postgres":
        return PostgresIdentityBindingRepository()
    return _DEFAULT_BINDING_REPO
