from __future__ import annotations

import pytest

from aicrm_next.insights.data_health.checks import (
    _current_identity_contract_violations,
    _identity_legacy_column_guard,
)


pytestmark = pytest.mark.contract


def test_identity_health_guard_executes_the_current_code_contract() -> None:
    _current_identity_contract_violations.cache_clear()
    assert _current_identity_contract_violations() == ()
    result = _identity_legacy_column_guard()
    assert result.status == "ok"
    assert result.severity == "green"
