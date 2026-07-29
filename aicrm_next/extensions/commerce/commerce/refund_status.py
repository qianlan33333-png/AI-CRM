from __future__ import annotations


TERMINAL_REFUND_STATUSES = frozenset({"failed", "closed", "abnormal", "success"})
TERMINAL_REFUND_JOB_STATUSES = frozenset({"failed_terminal", "blocked", "cancelled", "expired"})


def active_wechat_refund_sql(refund_alias: str = "r") -> str:
    alias = str(refund_alias or "r").strip()
    return f"""
        LOWER(COALESCE({alias}.status, '')) NOT IN ('failed', 'closed', 'abnormal', 'success')
        AND NOT EXISTS (
            SELECT 1
            FROM external_effect_job j
            WHERE j.target_type = 'wechat_pay_refund'
              AND j.target_id = {alias}.out_refund_no
              AND j.status IN ('failed_terminal', 'blocked', 'cancelled', 'expired')
        )
    """
