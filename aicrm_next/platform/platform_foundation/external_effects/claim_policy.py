from __future__ import annotations


def external_canary_authorization_predicate(*, row_alias: str = "job") -> str:
    """Return the durable SQL proof required by an allowlisted canary row."""

    authorization = (
        f"COALESCE({row_alias}.payload_summary_json, '{{}}'::jsonb)"
        "->'canary_authorization'"
    )
    authorized_job_id = f"""
        CASE
            WHEN jsonb_typeof({authorization}->'authorized_job_id') = 'number'
             AND ({authorization}->>'authorized_job_id') ~ '^[1-9][0-9]*$'
            THEN ({authorization}->>'authorized_job_id')::numeric
            ELSE 0::numeric
        END
    """
    authorized_from_version = f"""
        CASE
            WHEN jsonb_typeof({authorization}->'authorized_from_version') = 'number'
             AND ({authorization}->>'authorized_from_version') ~ '^[1-9][0-9]*$'
            THEN ({authorization}->>'authorized_from_version')::numeric
            ELSE 0::numeric
        END
    """
    return f"""
        (
            jsonb_typeof({authorization}) = 'object'
            AND COALESCE({authorization}->>'actor', '') <> ''
            AND COALESCE({authorization}->>'reason', '') <> ''
            AND COALESCE({authorization}->>'authorized_at', '') <> ''
            AND ({authorized_job_id}) = {row_alias}.id::numeric
            AND ({authorized_from_version}) >= 1::numeric
            AND {row_alias}.row_version::numeric >= ({authorized_from_version}) + 1::numeric
            AND {authorization}->'duplicate_risk_confirmed' = 'false'::jsonb
        )
    """


def external_claim_scope_predicate(
    *,
    row_alias: str = "job",
    scope_expression: str = "control.external_claim_scope",
    execution_scope_expression: str | None = None,
    canary_authorized_expression: str | None = None,
) -> str:
    """Return the canonical SQL gate for external-effect execution scope."""

    execution_scope = execution_scope_expression or (
        f"COALESCE({row_alias}.payload_json->>'execution_scope', '')"
    )
    canary_authorized = canary_authorized_expression or (
        external_canary_authorization_predicate(row_alias=row_alias)
    )
    return f"""
        (
            (
                {execution_scope} <> 'allowlisted_canary'
                OR ({canary_authorized})
            )
            AND (
                {scope_expression} = 'all'
                OR (
                    {scope_expression} = 'allowlisted'
                    AND {execution_scope} IN ('test_loopback', 'allowlisted_canary')
                )
                OR (
                    {scope_expression} = 'test_loopback'
                    AND {execution_scope} = 'test_loopback'
                )
            )
        )
    """


def external_effect_claim_order_sql(
    *,
    row_alias: str = "job",
    fairness_alias: str = "fairness",
) -> str:
    """Return the canonical external-effect fairness/priority/due ordering."""

    return (
        f"COALESCE({fairness_alias}.last_claimed_at, '-infinity'::timestamptz), "
        f"{row_alias}.priority ASC, {row_alias}.available_at ASC, {row_alias}.id ASC"
    )


__all__ = [
    "external_canary_authorization_predicate",
    "external_claim_scope_predicate",
    "external_effect_claim_order_sql",
]
