from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.check_import_graph import check_import_graph, load_baseline, scan_import_graph


ROOT = Path(__file__).resolve().parents[1]


def _write_module(root: Path, module_path: str, content: str = "") -> Path:
    package = root / "aicrm_next"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").touch()
    path = package / module_path
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.parent
    while current != package:
        (current / "__init__.py").touch()
        current = current.parent
    path.write_text(content, encoding="utf-8")
    return path


def _write_baseline(
    path: Path,
    *,
    components: list[list[str]],
    max_contexts: int = 20,
    max_edges: int = 20,
    max_cyclic_contexts: int = 20,
) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "package": "aicrm_next",
                "limits": {
                    "max_contexts": max_contexts,
                    "max_cross_context_edges": max_edges,
                    "max_cyclic_contexts": max_cyclic_contexts,
                },
                "allowed_cyclic_components": [
                    {
                        "id": f"legacy_scc_{index}",
                        "owner": "architecture",
                        "reason": "Existing cycle under active removal.",
                        "remove_by": "2026-08-31",
                        "contexts": contexts,
                    }
                    for index, contexts in enumerate(components, start=1)
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _edge_pairs(report) -> set[tuple[str, str]]:
    return {(edge.source_context, edge.target_context) for edge in report.edges}


def test_scanner_resolves_runtime_absolute_relative_local_and_literal_dynamic_imports(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "alpha/service.py",
        """
from typing import TYPE_CHECKING
import importlib
from aicrm_next.beta.service import Beta
from ..gamma.contract import Gamma

if TYPE_CHECKING:
    from aicrm_next.ignored.contract import Ignored

def load_runtime_dependencies():
    import aicrm_next.delta.worker
    return importlib.import_module("aicrm_next.epsilon.adapter")
""",
    )
    for context in ("beta", "gamma", "delta", "epsilon", "ignored"):
        _write_module(tmp_path, f"{context}/contract.py")

    report = scan_import_graph(tmp_path)

    assert _edge_pairs(report) == {
        ("alpha", "beta"),
        ("alpha", "gamma"),
        ("alpha", "delta"),
        ("alpha", "epsilon"),
    }
    assert report.non_literal_dynamic_imports == ()
    alpha_beta = next(edge for edge in report.edges if (edge.source_context, edge.target_context) == ("alpha", "beta"))
    assert alpha_beta.evidence[0].path == "aicrm_next/alpha/service.py"
    assert alpha_beta.evidence[0].line == 4


def test_scanner_calculates_deterministic_cross_context_scc(tmp_path: Path) -> None:
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "beta/service.py", "from ..alpha.service import Alpha\n")

    first = scan_import_graph(tmp_path)
    second = scan_import_graph(tmp_path)

    assert first.to_dict() == second.to_dict()
    assert first.cyclic_components == (("alpha", "beta"),)
    assert _edge_pairs(first) == {("alpha", "beta"), ("beta", "alpha")}


def test_package_root_composition_imports_are_not_a_business_context(tmp_path: Path) -> None:
    _write_module(tmp_path, "main.py", "from aicrm_next.alpha.service import Alpha\n")
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "beta/service.py")

    report = scan_import_graph(tmp_path)

    assert report.contexts == ("alpha", "beta")
    assert _edge_pairs(report) == {("alpha", "beta")}


def test_business_context_import_of_package_root_composition_is_not_a_context_edge(tmp_path: Path) -> None:
    _write_module(tmp_path, "composition.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.composition import build\n")
    _write_module(tmp_path, "beta/service.py")

    report = scan_import_graph(tmp_path)

    assert report.contexts == ("alpha", "beta")
    assert _edge_pairs(report) == set()


def test_existing_registered_scc_is_allowed(tmp_path: Path) -> None:
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "beta/service.py", "from aicrm_next.alpha.service import Alpha\n")
    baseline = tmp_path / "baseline.yml"
    _write_baseline(baseline, components=[["alpha", "beta"]])

    report, violations = check_import_graph(root=tmp_path, baseline_path=baseline)

    assert report.cyclic_components == (("alpha", "beta"),)
    assert violations == []


def test_registered_scc_shrink_or_split_is_allowed(tmp_path: Path) -> None:
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "beta/service.py", "from aicrm_next.alpha.service import Alpha\n")
    _write_module(tmp_path, "gamma/service.py")
    baseline = tmp_path / "baseline.yml"
    _write_baseline(baseline, components=[["alpha", "beta", "gamma"]])

    report, violations = check_import_graph(root=tmp_path, baseline_path=baseline)

    assert report.cyclic_components == (("alpha", "beta"),)
    assert violations == []


def test_new_context_joining_registered_scc_is_blocked_with_evidence(tmp_path: Path) -> None:
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "beta/service.py", "from aicrm_next.gamma.service import Gamma\n")
    _write_module(tmp_path, "gamma/service.py", "from aicrm_next.alpha.service import Alpha\n")
    baseline = tmp_path / "baseline.yml"
    _write_baseline(baseline, components=[["alpha", "beta"]])

    _, violations = check_import_graph(root=tmp_path, baseline_path=baseline)

    violation = next(item for item in violations if item.rule == "scc_expanded")
    assert "gamma" in violation.reason
    assert {item.path for item in violation.evidence} == {
        "aicrm_next/alpha/service.py",
        "aicrm_next/beta/service.py",
        "aicrm_next/gamma/service.py",
    }


def test_new_scc_outside_registered_component_is_blocked(tmp_path: Path) -> None:
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "beta/service.py", "from aicrm_next.alpha.service import Alpha\n")
    _write_module(tmp_path, "gamma/service.py", "from aicrm_next.delta.service import Delta\n")
    _write_module(tmp_path, "delta/service.py", "from aicrm_next.gamma.service import Gamma\n")
    baseline = tmp_path / "baseline.yml"
    _write_baseline(baseline, components=[["alpha", "beta"]])

    _, violations = check_import_graph(root=tmp_path, baseline_path=baseline)

    violation = next(item for item in violations if item.rule == "unregistered_scc")
    assert "delta" in violation.reason
    assert "gamma" in violation.reason


def test_non_literal_dynamic_import_fails_closed(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "alpha/service.py",
        """
import importlib as loader

def load(module_name):
    return loader.import_module(module_name)
""",
    )
    baseline = tmp_path / "baseline.yml"
    _write_baseline(baseline, components=[])

    report, violations = check_import_graph(root=tmp_path, baseline_path=baseline)

    assert len(report.non_literal_dynamic_imports) == 1
    violation = next(item for item in violations if item.rule == "non_literal_dynamic_import")
    assert violation.evidence[0].path == "aicrm_next/alpha/service.py"
    assert violation.evidence[0].line == 5


def test_context_and_edge_budgets_are_monotonic(tmp_path: Path) -> None:
    _write_module(tmp_path, "alpha/service.py", "from aicrm_next.beta.service import Beta\n")
    _write_module(tmp_path, "beta/service.py")
    baseline = tmp_path / "baseline.yml"
    _write_baseline(baseline, components=[], max_contexts=1, max_edges=0, max_cyclic_contexts=0)

    _, violations = check_import_graph(root=tmp_path, baseline_path=baseline)

    assert {item.rule for item in violations} == {"context_budget_exceeded", "edge_budget_exceeded"}


def test_baseline_requires_owned_time_bounded_scc_entries(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.yml"
    baseline.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "package": "aicrm_next",
                "limits": {
                    "max_contexts": 2,
                    "max_cross_context_edges": 2,
                    "max_cyclic_contexts": 2,
                },
                "allowed_cyclic_components": [{"id": "missing_ownership", "contexts": ["alpha", "beta"]}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="owner"):
        load_baseline(baseline)


def test_admin_read_model_does_not_reverse_import_admin_config() -> None:
    report = scan_import_graph(ROOT)

    assert ("admin_read_model", "admin_config") not in _edge_pairs(report)


def test_platform_foundation_does_not_reverse_import_external_effect_business_continuations() -> None:
    report = scan_import_graph(ROOT)

    assert ("platform_foundation", "automation_agents") not in _edge_pairs(report)
    assert ("platform_foundation", "customer_tags") not in _edge_pairs(report)


def test_ai_audience_ops_does_not_import_ops_enrollment_runtime() -> None:
    report = scan_import_graph(ROOT)

    assert ("ai_audience_ops", "ops_enrollment") not in _edge_pairs(report)


def test_repository_import_graph_matches_registered_r12_baseline() -> None:
    report, violations = check_import_graph(
        root=ROOT,
        baseline_path=ROOT / "docs" / "architecture" / "import_graph_baseline.yml",
    )

    assert violations == []
    assert len(report.contexts) == 40
    assert len(report.edges) == 148
    assert report.cyclic_components == ()
    assert report.cyclic_context_count == 0
