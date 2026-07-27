from __future__ import annotations


# Stable release boundary shared by questionnaire runtime, reconciliation, and
# data-health checks. Keep the SQL literal beside the public timestamp so every
# context evaluates the same production ownership window without importing
# another business context.
QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_AT = "2026-07-13T16:20:00Z"
QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL = "TIMESTAMPTZ '2026-07-13 16:20:00+00'"
