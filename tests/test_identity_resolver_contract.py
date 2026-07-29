from __future__ import annotations

import pytest

from aicrm_next.crm.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.crm.identity_contact.resolver import DBAPIIdentityResolver, classify_identity_candidates


def _candidate(unionid: str, **matches):
    return {
        "unionid": unionid,
        "external_userid": f"external-{unionid}",
        "openid": f"openid-{unionid}",
        "mobile": "13800138000",
        "mobile_verified": True,
        "owner_userid": "owner",
        "status": "active",
        "matched_unionid": False,
        "matched_external_userid": False,
        "matched_openid": False,
        "matched_mobile": False,
        **matches,
    }


@pytest.mark.parametrize(
    ("identity_query", "match_field"),
    [
        (ResolvePersonIdentityRequest(unionid="union-1"), "matched_unionid"),
        (ResolvePersonIdentityRequest(external_userid="external-1"), "matched_external_userid"),
        (ResolvePersonIdentityRequest(openid="openid-1"), "matched_openid"),
        (ResolvePersonIdentityRequest(mobile="+86 138-0013-8000"), "matched_mobile"),
    ],
)
def test_single_alias_resolves_one_active_canonical_identity(identity_query, match_field) -> None:
    result = classify_identity_candidates(identity_query, [_candidate("union-1", **{match_field: True})])

    assert result.status == "resolved"
    assert result.identity is not None
    assert result.identity.unionid == "union-1"
    assert result.candidate_count == 1


def test_multiple_aliases_must_all_resolve_to_the_same_unionid() -> None:
    request = ResolvePersonIdentityRequest(
        unionid="union-1",
        external_userid="external-1",
        openid="openid-1",
        mobile="13800138000",
    )
    result = classify_identity_candidates(
        request,
        [
            _candidate(
                "union-1",
                matched_unionid=True,
                matched_external_userid=True,
                matched_openid=True,
                matched_mobile=True,
            )
        ],
    )

    assert result.status == "resolved"
    assert result.matched_fields == ["unionid", "external_userid", "openid", "mobile"]


def test_cross_field_identity_disagreement_fails_closed() -> None:
    request = ResolvePersonIdentityRequest(external_userid="external-1", openid="openid-2")
    result = classify_identity_candidates(
        request,
        [
            _candidate("union-1", matched_external_userid=True),
            _candidate("union-2", matched_openid=True),
        ],
    )

    assert result.status == "conflict"
    assert result.reason == "identity_inputs_disagree"
    assert result.identity is None


def test_duplicate_alias_does_not_select_the_most_recent_row() -> None:
    request = ResolvePersonIdentityRequest(external_userid="duplicate-external")
    result = classify_identity_candidates(
        request,
        [
            _candidate("union-older", matched_external_userid=True),
            _candidate("union-newer", matched_external_userid=True),
        ],
    )

    assert result.status == "conflict"
    assert result.reason == "duplicate_alias"
    assert result.candidate_count == 2


def test_non_active_canonical_identity_is_a_conflict() -> None:
    row = _candidate("union-deleted", matched_unionid=True)
    row["status"] = "deleted"

    result = classify_identity_candidates(ResolvePersonIdentityRequest(unionid="union-deleted"), [row])

    assert result.status == "conflict"
    assert result.reason == "canonical_identity_not_active"


def test_missing_alias_is_pending_when_resolution_queue_has_work() -> None:
    request = ResolvePersonIdentityRequest(external_userid="external-pending")
    result = classify_identity_candidates(request, [], pending_count=1)

    assert result.status == "pending"
    assert result.pending_count == 1


class _Executor:
    def __init__(self, candidates, pending_count=0) -> None:
        self.candidates = list(candidates)
        self.pending_count = pending_count
        self.queries: list[tuple[str, tuple]] = []
        self._rows = []

    def execute(self, query, params):
        self.queries.append((query, tuple(params)))
        self._rows = [{"pending_count": self.pending_count}] if "resolution_queue" in query else self.candidates
        return self

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_dbapi_resolver_collects_all_candidates_in_one_query_without_first_match() -> None:
    executor = _Executor([_candidate("union-1", matched_external_userid=True)])

    result = DBAPIIdentityResolver(executor).resolve(ResolvePersonIdentityRequest(external_userid="external-1"))

    assert result.status == "resolved"
    assert len(executor.queries) == 1
    candidate_sql, params = executor.queries[0]
    assert "FROM crm_user_identity identity" in candidate_sql
    assert "LIMIT 1" not in candidate_sql
    assert "ORDER BY identity.unionid" in candidate_sql
    assert params == ("external-1", "", "", "")
