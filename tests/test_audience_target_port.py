from __future__ import annotations

from aicrm_next.automation.ops_enrollment import audience_target_port


def test_audience_target_query_is_supplied_by_composition_root(monkeypatch) -> None:
    class Query:
        def rows_for_package(self, package_id: int) -> list[dict]:
            return [{"id": package_id}]

    monkeypatch.setattr(audience_target_port, "_FACTORY", audience_target_port._FACTORY)
    audience_target_port.configure_audience_target_query(Query)

    assert audience_target_port.build_audience_target_query().rows_for_package(17) == [{"id": 17}]
