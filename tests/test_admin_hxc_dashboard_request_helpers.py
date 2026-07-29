"""HXC admin request parsing helpers now owned by Next safe-mode."""
from __future__ import annotations

import pytest

from aicrm_next.extensions.hxc.hxc_dashboard.safe_mode import (
    HxcDashboardInputError,
    normalize_external_userids,
    normalize_image_library_ids,
    normalize_optional_int,
    normalize_priority,
)


def test_hxc_request_fields_use_next_safe_mode_normalizers():
    payload = {
        "priority": "42",
        "miniprogram_library_id": "12",
        "external_userids": "ext1",
        "image_library_ids": ["1", "bad", 2, 3, 4],
    }

    assert normalize_priority(payload["priority"], default=100) == 42
    assert normalize_optional_int(payload["miniprogram_library_id"]) == 12
    assert normalize_external_userids(payload["external_userids"]) == ["ext1"]
    assert normalize_image_library_ids(payload["image_library_ids"]) == [1, 2]


def test_hxc_request_fields_reject_bad_priority_in_next_contract():
    with pytest.raises(HxcDashboardInputError):
        normalize_priority("bad", default=100)
