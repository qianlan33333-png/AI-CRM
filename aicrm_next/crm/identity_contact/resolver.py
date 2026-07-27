from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from aicrm_next.shared.errors import ApplicationError

from .domain import normalize_identity_request
from .dto import (
    ContactPoint,
    IdentityResolution,
    IdentityResolveResult,
    ResolvePersonIdentityRequest,
)


class IdentityConflictError(ApplicationError):
    status_code = 409


class IdentityResolver(Protocol):
    def resolve(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult: ...


_FIELDS = ("unionid", "external_userid", "openid", "mobile")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row or {})
    except (TypeError, ValueError):
        return {}


def _provided_fields(query: ResolvePersonIdentityRequest) -> list[str]:
    return [field for field in _FIELDS if _text(getattr(query, field, None))]


def _alias_values(value: Any, *, object_key: str) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return set()
    if not isinstance(value, list):
        return set()
    values: set[str] = set()
    for item in value:
        alias = _text(item.get(object_key)) if isinstance(item, Mapping) else _text(item)
        if alias:
            values.add(alias)
    return values


def _row_matches(row: Mapping[str, Any], field: str, query: ResolvePersonIdentityRequest) -> bool:
    explicit_key = f"matched_{field}"
    if explicit_key in row:
        return bool(row.get(explicit_key))
    expected = _text(getattr(query, field, None))
    if not expected:
        return False
    if field == "unionid":
        return _text(row.get("unionid")) == expected
    if field == "external_userid":
        primary = _text(row.get("external_userid") or row.get("primary_external_userid"))
        return primary == expected or expected in _alias_values(row.get("external_userids_json"), object_key="external_userid")
    if field == "openid":
        primary = _text(row.get("openid") or row.get("primary_openid"))
        return primary == expected or expected in _alias_values(row.get("openids_json"), object_key="openid")
    return _text(row.get("mobile_normalized") or row.get("mobile")) == expected


def _identity_from_row(row: Mapping[str, Any], *, matched_fields: list[str]) -> IdentityResolution:
    unionid = _text(row.get("unionid")) or None
    external_userid = _text(row.get("external_userid") or row.get("primary_external_userid")) or None
    openid = _text(row.get("openid") or row.get("primary_openid")) or None
    mobile = _text(row.get("mobile") or row.get("mobile_normalized")) or None
    owner_userid = _text(row.get("owner_userid") or row.get("primary_owner_userid")) or None
    contact_points: list[ContactPoint] = []
    if unionid:
        contact_points.append(ContactPoint(type="wechat_unionid", value=unionid, verified=True))
    if external_userid:
        contact_points.append(ContactPoint(type="wecom_external_userid", value=external_userid, verified=True))
    if openid:
        contact_points.append(ContactPoint(type="wechat_openid", value=openid, verified=True))
    if mobile:
        contact_points.append(ContactPoint(type="mobile", value=mobile, verified=bool(row.get("mobile_verified"))))
    profile = row.get("profile_json")
    profile_description = _text(profile.get("description")) if isinstance(profile, Mapping) else ""
    return IdentityResolution(
        person_id=_text(row.get("person_id")) or None,
        external_userid=external_userid,
        mobile=mobile,
        openid=openid,
        unionid=unionid,
        customer_name=_text(row.get("customer_name")) or None,
        remark=_text(row.get("remark")) or None,
        description=_text(row.get("description") or profile_description) or None,
        mobile_source=_text(row.get("mobile_source")) or None,
        binding_status="bound",
        owner_userid=owner_userid,
        identity_map_id=None,
        follow_user_userid=owner_userid,
        matched_by="+".join(matched_fields),
        contact_points=contact_points,
    )


def classify_identity_candidates(
    query: ResolvePersonIdentityRequest,
    rows: Iterable[Mapping[str, Any]],
    *,
    pending_count: int = 0,
) -> IdentityResolveResult:
    """Classify every alias candidate without using lookup order or row recency."""

    normalized = normalize_identity_request(query)
    fields = _provided_fields(normalized)
    candidates = [_row_dict(row) for row in rows]
    candidates = [row for row in candidates if _text(row.get("unionid"))]
    if not fields:
        return IdentityResolveResult(status="not_found", reason="identity_input_missing")

    by_field: dict[str, set[str]] = {
        field: {
            _text(row.get("unionid"))
            for row in candidates
            if _row_matches(row, field, normalized) and _text(row.get("unionid"))
        }
        for field in fields
    }
    all_unionids = set().union(*by_field.values()) if by_field else set()
    duplicate_fields = [field for field, unionids in by_field.items() if len(unionids) > 1]
    if duplicate_fields:
        return IdentityResolveResult(
            status="conflict",
            reason="duplicate_alias",
            matched_fields=fields,
            candidate_count=len(all_unionids),
            pending_count=max(0, int(pending_count)),
        )

    nonempty_sets = [unionids for unionids in by_field.values() if unionids]
    if len(nonempty_sets) > 1 and any(unionids != nonempty_sets[0] for unionids in nonempty_sets[1:]):
        return IdentityResolveResult(
            status="conflict",
            reason="identity_inputs_disagree",
            matched_fields=fields,
            candidate_count=len(all_unionids),
            pending_count=max(0, int(pending_count)),
        )

    if any(not unionids for unionids in by_field.values()):
        return IdentityResolveResult(
            status="pending" if pending_count else "not_found",
            reason="identity_pending_resolution" if pending_count else "identity_not_found",
            matched_fields=fields,
            candidate_count=len(all_unionids),
            pending_count=max(0, int(pending_count)),
        )

    if len(all_unionids) != 1:
        return IdentityResolveResult(
            status="conflict" if all_unionids else ("pending" if pending_count else "not_found"),
            reason="identity_candidates_not_singleton" if all_unionids else "identity_not_found",
            matched_fields=fields,
            candidate_count=len(all_unionids),
            pending_count=max(0, int(pending_count)),
        )

    unionid = next(iter(all_unionids))
    matching_rows = [row for row in candidates if _text(row.get("unionid")) == unionid]
    if len(matching_rows) != 1:
        return IdentityResolveResult(
            status="conflict",
            reason="canonical_row_not_singleton",
            matched_fields=fields,
            candidate_count=len(matching_rows),
            pending_count=max(0, int(pending_count)),
        )
    row = matching_rows[0]
    if _text(row.get("status") or "active").lower() != "active":
        return IdentityResolveResult(
            status="conflict",
            reason="canonical_identity_not_active",
            matched_fields=fields,
            candidate_count=1,
            pending_count=max(0, int(pending_count)),
        )
    return IdentityResolveResult(
        status="resolved",
        identity=_identity_from_row(row, matched_fields=fields),
        matched_fields=fields,
        candidate_count=1,
        pending_count=max(0, int(pending_count)),
    )


def _candidate_sql(
    placeholder: str | tuple[str, str, str, str],
    *,
    fields: Iterable[str] = _FIELDS,
) -> str:
    placeholders = (placeholder,) * 4 if isinstance(placeholder, str) else placeholder
    requested_fields = tuple(dict.fromkeys(str(field) for field in fields))
    invalid_fields = sorted(set(requested_fields) - set(_FIELDS))
    if invalid_fields:
        raise ValueError(f"unsupported identity candidate fields: {', '.join(invalid_fields)}")
    conditions = {
        "unionid": "identity.unionid = input.unionid",
        "external_userid": """(
                identity.primary_external_userid = input.external_userid
                OR identity.external_userids_json @> jsonb_build_array(input.external_userid)
                OR identity.external_userids_json @> jsonb_build_array(
                    jsonb_build_object('external_userid', input.external_userid)
                )
            )""",
        "openid": """(
                identity.primary_openid = input.openid
                OR identity.openids_json @> jsonb_build_array(input.openid)
                OR identity.openids_json @> jsonb_build_array(
                    jsonb_build_object('openid', input.openid)
                )
            )""",
        "mobile": "identity.mobile_normalized = input.mobile",
    }
    where_clause = "\n           OR ".join(conditions[field] for field in requested_fields) or "FALSE"
    return f"""
        WITH identity_input AS (
            SELECT CAST({placeholders[0]} AS text) AS external_userid,
                   CAST({placeholders[1]} AS text) AS unionid,
                   CAST({placeholders[2]} AS text) AS openid,
                   CAST({placeholders[3]} AS text) AS mobile
        )
        SELECT identity.unionid,
               identity.primary_external_userid AS external_userid,
               identity.primary_openid AS openid,
               identity.primary_owner_userid AS owner_userid,
               identity.legacy_person_id AS person_id,
               identity.mobile,
               identity.mobile_normalized,
               identity.mobile_verified,
               identity.mobile_source,
               identity.customer_name,
               identity.remark,
               identity.description,
               identity.profile_json,
               identity.identity_status AS status,
               (input.unionid <> '' AND identity.unionid = input.unionid) AS matched_unionid,
               (input.external_userid <> '' AND (
                    identity.primary_external_userid = input.external_userid
                    OR identity.external_userids_json @> jsonb_build_array(input.external_userid)
                    OR identity.external_userids_json @> jsonb_build_array(
                        jsonb_build_object('external_userid', input.external_userid)
                    )
               )) AS matched_external_userid,
               (input.openid <> '' AND (
                    identity.primary_openid = input.openid
                    OR identity.openids_json @> jsonb_build_array(input.openid)
                    OR identity.openids_json @> jsonb_build_array(
                        jsonb_build_object('openid', input.openid)
                    )
               )) AS matched_openid,
               (input.mobile <> '' AND identity.mobile_normalized = input.mobile) AS matched_mobile
        FROM crm_user_identity identity
        CROSS JOIN identity_input input
        WHERE {where_clause}
        ORDER BY identity.unionid
    """


def _pending_sql(placeholder: str | tuple[str, str, str]) -> str:
    placeholders = (placeholder,) * 3 if isinstance(placeholder, str) else placeholder
    return f"""
        WITH identity_input AS (
            SELECT CAST({placeholders[0]} AS text) AS external_userid,
                   CAST({placeholders[1]} AS text) AS openid,
                   CAST({placeholders[2]} AS text) AS mobile
        )
        SELECT COUNT(*) AS pending_count
        FROM crm_user_identity_resolution_queue queue
        CROSS JOIN identity_input input
        WHERE queue.status IN ('pending', 'polling')
          AND (
              (input.external_userid <> '' AND queue.external_userid = input.external_userid)
              OR (input.openid <> '' AND queue.openid = input.openid)
              OR (input.mobile <> '' AND queue.mobile = input.mobile)
          )
    """


def _fetchall(executor: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    result = executor.execute(sql, params)
    source = result if hasattr(result, "fetchall") or hasattr(result, "fetchone") else executor
    if hasattr(source, "fetchall"):
        return [_row_dict(row) for row in list(source.fetchall() or [])]
    row = source.fetchone()
    return [_row_dict(row)] if row else []


def _fetchone(executor: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    result = executor.execute(sql, params)
    source = result if hasattr(result, "fetchone") or hasattr(result, "fetchall") else executor
    if hasattr(source, "fetchone"):
        return _row_dict(source.fetchone())
    rows = list(source.fetchall() or [])
    return _row_dict(rows[0] if rows else None)


class DBAPIIdentityResolver:
    def __init__(self, executor: Any, *, placeholder: str = "%s", for_update: bool = False) -> None:
        self._executor = executor
        self._placeholder = placeholder
        self._for_update = bool(for_update)

    def resolve(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult:
        normalized = normalize_identity_request(query)
        fields = _provided_fields(normalized)
        params = (
            _text(normalized.external_userid),
            _text(normalized.unionid),
            _text(normalized.openid),
            _text(normalized.mobile),
        )
        candidate_sql = _candidate_sql(self._placeholder, fields=fields)
        if self._for_update:
            candidate_sql += "\n        FOR UPDATE OF identity"
        rows = _fetchall(self._executor, candidate_sql, params)
        provisional = classify_identity_candidates(normalized, rows)
        if provisional.status in {"resolved", "conflict"}:
            return provisional
        pending = _fetchone(
            self._executor,
            _pending_sql(self._placeholder),
            (
                _text(normalized.external_userid),
                _text(normalized.openid),
                _text(normalized.mobile),
            ),
        )
        return classify_identity_candidates(normalized, rows, pending_count=int(pending.get("pending_count") or 0))


class PostgresIdentityResolver:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def resolve(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult:
        with self._connection_factory() as connection:
            return DBAPIIdentityResolver(connection).resolve(query)


class SQLAlchemyIdentityResolver:
    def __init__(self, executor: Any) -> None:
        self._executor = executor

    def resolve(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult:
        from sqlalchemy import text

        normalized = normalize_identity_request(query)
        fields = _provided_fields(normalized)
        params = {field: _text(getattr(normalized, field)) for field in _FIELDS}
        rows = self._executor.execute(
            text(
                _candidate_sql(
                    (":external_userid", ":unionid", ":openid", ":mobile"),
                    fields=fields,
                )
            ),
            params,
        ).mappings().all()
        provisional = classify_identity_candidates(normalized, rows)
        if provisional.status in {"resolved", "conflict"}:
            return provisional
        pending = self._executor.execute(
            text(_pending_sql((":external_userid", ":openid", ":mobile"))),
            params,
        ).mappings().first()
        return classify_identity_candidates(
            normalized,
            rows,
            pending_count=int((pending or {}).get("pending_count") or 0),
        )


def resolve_external_userids_with_sqlalchemy(
    executor: Any,
    external_userids: Iterable[str],
) -> dict[str, IdentityResolveResult]:
    """Batch alias adapter for consumers that already own a SQLAlchemy transaction."""

    from sqlalchemy import text

    normalized_values = list(dict.fromkeys(_text(value) for value in external_userids if _text(value)))
    if not normalized_values:
        return {}
    rows = executor.execute(
        text(
            """
            WITH identity_input AS (
                SELECT unnest(CAST(:external_userids AS text[])) AS external_userid
            )
            SELECT input.external_userid AS input_external_userid,
                   identity.unionid,
                   identity.primary_external_userid AS external_userid,
                   identity.primary_openid AS openid,
                   identity.primary_owner_userid AS owner_userid,
                   identity.legacy_person_id AS person_id,
                   identity.mobile,
                   identity.mobile_normalized,
                   identity.mobile_verified,
                   identity.identity_status AS status,
                   FALSE AS matched_unionid,
                   TRUE AS matched_external_userid,
                   FALSE AS matched_openid,
                   FALSE AS matched_mobile
            FROM identity_input input
            JOIN crm_user_identity identity
              ON identity.primary_external_userid = input.external_userid
              OR identity.external_userids_json @> jsonb_build_array(input.external_userid)
              OR identity.external_userids_json @> jsonb_build_array(
                    jsonb_build_object('external_userid', input.external_userid)
              )
            ORDER BY input.external_userid, identity.unionid
            """
        ),
        {"external_userids": normalized_values},
    ).mappings().all()
    by_input: dict[str, list[Mapping[str, Any]]] = {value: [] for value in normalized_values}
    for row in rows:
        input_external_userid = _text(row.get("input_external_userid"))
        if input_external_userid in by_input:
            by_input[input_external_userid].append(row)
    return {
        external_userid: classify_identity_candidates(
            ResolvePersonIdentityRequest(external_userid=external_userid),
            candidates,
        )
        for external_userid, candidates in by_input.items()
    }


def resolved_unionids_for_external_userids_with_sqlalchemy(
    executor: Any,
    external_userids: Iterable[str],
) -> list[str]:
    normalized_values = list(dict.fromkeys(_text(value) for value in external_userids if _text(value)))
    resolutions = resolve_external_userids_with_sqlalchemy(executor, normalized_values)
    unionids: list[str] = []
    for external_userid in normalized_values:
        unionid = resolved_unionid(resolutions[external_userid])
        if unionid and unionid not in unionids:
            unionids.append(unionid)
    return unionids


def resolve_identity_with_dbapi(
    executor: Any,
    query: ResolvePersonIdentityRequest,
    *,
    placeholder: str = "%s",
    for_update: bool = False,
) -> IdentityResolveResult:
    return DBAPIIdentityResolver(executor, placeholder=placeholder, for_update=for_update).resolve(query)


def resolve_external_userid_with_dbapi(
    executor: Any,
    external_userid: str,
    *,
    placeholder: str = "%s",
    for_update: bool = False,
) -> str:
    return resolved_unionid(
        resolve_identity_with_dbapi(
            executor,
            ResolvePersonIdentityRequest(external_userid=_text(external_userid) or None),
            placeholder=placeholder,
            for_update=for_update,
        )
    )


def resolve_external_userid_with_sqlalchemy(executor: Any, external_userid: str) -> str:
    return resolved_unionid(
        SQLAlchemyIdentityResolver(executor).resolve(
            ResolvePersonIdentityRequest(external_userid=_text(external_userid) or None)
        )
    )


def resolved_identity_or_none(result: IdentityResolveResult) -> IdentityResolution | None:
    if result.status == "resolved":
        return result.identity
    if result.status == "conflict":
        raise IdentityConflictError(result.reason or "identity conflict")
    return None


def resolved_unionid(result: IdentityResolveResult) -> str:
    if result.status != "resolved" or result.identity is None:
        return ""
    return _text(result.identity.unionid)
