#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "docs" / "architecture" / "import_graph_baseline.yml"
STABLE_DOMAIN_PACKAGES = {
    "app",
    "platform",
    "channels",
    "crm",
    "engagement",
    "automation",
    "insights",
    "extensions",
}


@dataclass(frozen=True, order=True)
class ImportEvidence:
    source_context: str
    target_context: str
    path: str
    line: int
    module: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_context": self.source_context,
            "target_context": self.target_context,
            "path": self.path,
            "line": self.line,
            "module": self.module,
            "kind": self.kind,
        }


@dataclass(frozen=True, order=True)
class ContextImportEdge:
    source_context: str
    target_context: str
    evidence: tuple[ImportEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_context": self.source_context,
            "target_context": self.target_context,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class ImportGraphReport:
    contexts: tuple[str, ...]
    edges: tuple[ContextImportEdge, ...]
    cyclic_components: tuple[tuple[str, ...], ...]
    non_literal_dynamic_imports: tuple[ImportEvidence, ...]

    @property
    def cyclic_context_count(self) -> int:
        return sum(len(component) for component in self.cyclic_components)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_count": len(self.contexts),
            "cross_context_edge_count": len(self.edges),
            "cyclic_component_count": len(self.cyclic_components),
            "cyclic_context_count": self.cyclic_context_count,
            "contexts": list(self.contexts),
            "edges": [edge.to_dict() for edge in self.edges],
            "cyclic_components": [list(component) for component in self.cyclic_components],
            "non_literal_dynamic_imports": [item.to_dict() for item in self.non_literal_dynamic_imports],
        }


@dataclass(frozen=True)
class AllowedCyclicComponent:
    component_id: str
    owner: str
    reason: str
    remove_by: str
    contexts: frozenset[str]


@dataclass(frozen=True)
class ImportGraphBaseline:
    package: str
    max_contexts: int
    max_cross_context_edges: int
    max_cyclic_contexts: int
    allowed_cyclic_components: tuple[AllowedCyclicComponent, ...]


@dataclass(frozen=True)
class ImportGraphViolation:
    rule: str
    reason: str
    evidence: tuple[ImportEvidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "reason": self.reason,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    def format(self) -> str:
        lines = [f"{self.rule}: {self.reason}"]
        for item in self.evidence:
            lines.append(
                f"  {item.path}:{item.line}: {item.source_context} -> {item.target_context} "
                f"({item.kind}: {item.module})"
            )
        return "\n".join(lines)


def scan_import_graph(root: Path = ROOT, *, package: str = "aicrm_next") -> ImportGraphReport:
    root = Path(root).resolve()
    package_dir = root / package
    if not package_dir.is_dir():
        raise ValueError(f"runtime package directory does not exist: {package_dir}")

    contexts = {
        context
        for path in _iter_python_files(package_dir)
        if (context := _source_context(path.relative_to(package_dir).parts)) is not None
    }
    edge_evidence: dict[tuple[str, str], set[ImportEvidence]] = {}
    non_literal_dynamic_imports: set[ImportEvidence] = set()

    for path in _iter_python_files(package_dir):
        rel_to_package = path.relative_to(package_dir)
        source_context = _source_context(rel_to_package.parts)
        if source_context is None:
            # Direct package modules are composition-root/support modules, not business contexts.
            continue
        rel_to_root = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise ValueError(f"cannot scan import graph; syntax error in {rel_to_root}:{exc.lineno or 1}: {exc.msg}") from exc

        runtime_nodes = tuple(_iter_runtime_nodes(tree))
        importlib_aliases, import_module_aliases = _dynamic_import_aliases(runtime_nodes)
        for node in runtime_nodes:
            for module_name, kind in _imported_modules(node, path=path, package_dir=package_dir, package=package):
                target_context = _target_context(module_name, package)
                if not target_context or target_context == source_context or target_context not in contexts:
                    continue
                evidence = ImportEvidence(
                    source_context=source_context,
                    target_context=target_context,
                    path=rel_to_root,
                    line=int(getattr(node, "lineno", 1) or 1),
                    module=module_name,
                    kind=kind,
                )
                edge_evidence.setdefault((source_context, target_context), set()).add(evidence)

            dynamic = _dynamic_import(
                node,
                importlib_aliases=importlib_aliases,
                import_module_aliases=import_module_aliases,
            )
            if dynamic is None:
                continue
            module_name, kind, is_non_literal = dynamic
            if is_non_literal:
                non_literal_dynamic_imports.add(
                    ImportEvidence(
                        source_context=source_context,
                        target_context="<dynamic>",
                        path=rel_to_root,
                        line=int(getattr(node, "lineno", 1) or 1),
                        module=module_name,
                        kind=kind,
                    )
                )
                continue
            target_context = _target_context(module_name, package)
            if not target_context or target_context == source_context:
                continue
            evidence = ImportEvidence(
                source_context=source_context,
                target_context=target_context,
                path=rel_to_root,
                line=int(getattr(node, "lineno", 1) or 1),
                module=module_name,
                kind=kind,
            )
            edge_evidence.setdefault((source_context, target_context), set()).add(evidence)

    edges = tuple(
        ContextImportEdge(source, target, tuple(sorted(evidence)))
        for (source, target), evidence in sorted(edge_evidence.items())
    )
    graph = {context: set() for context in contexts}
    for edge in edges:
        graph.setdefault(edge.source_context, set()).add(edge.target_context)
        graph.setdefault(edge.target_context, set())
    cyclic_components = tuple(
        sorted(
            (tuple(sorted(component)) for component in _strongly_connected_components(graph) if len(component) > 1),
            key=lambda component: (-len(component), component),
        )
    )
    return ImportGraphReport(
        contexts=tuple(sorted(contexts)),
        edges=edges,
        cyclic_components=cyclic_components,
        non_literal_dynamic_imports=tuple(sorted(non_literal_dynamic_imports)),
    )


def load_baseline(path: Path = DEFAULT_BASELINE) -> ImportGraphBaseline:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("import graph baseline must be a mapping")
    if raw.get("schema_version") != 1:
        raise ValueError("import graph baseline schema_version must be 1")
    package = _required_text(raw, "package", location="baseline")
    limits = raw.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("baseline.limits must be a mapping")
    max_contexts = _non_negative_int(limits, "max_contexts", location="baseline.limits")
    max_edges = _non_negative_int(limits, "max_cross_context_edges", location="baseline.limits")
    max_cyclic_contexts = _non_negative_int(limits, "max_cyclic_contexts", location="baseline.limits")

    entries = raw.get("allowed_cyclic_components")
    if not isinstance(entries, list):
        raise ValueError("baseline.allowed_cyclic_components must be a list")
    components: list[AllowedCyclicComponent] = []
    claimed_contexts: set[str] = set()
    component_ids: set[str] = set()
    for index, entry in enumerate(entries):
        location = f"baseline.allowed_cyclic_components[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{location} must be a mapping")
        component_id = _required_text(entry, "id", location=location)
        owner = _required_text(entry, "owner", location=location)
        reason = _required_text(entry, "reason", location=location)
        remove_by = _required_text(entry, "remove_by", location=location)
        try:
            date.fromisoformat(remove_by)
        except ValueError as exc:
            raise ValueError(f"{location}.remove_by must use YYYY-MM-DD") from exc
        contexts_raw = entry.get("contexts")
        if not isinstance(contexts_raw, list) or len(contexts_raw) < 2:
            raise ValueError(f"{location}.contexts must contain at least two contexts")
        contexts = frozenset(str(item or "").strip() for item in contexts_raw)
        if "" in contexts or len(contexts) != len(contexts_raw):
            raise ValueError(f"{location}.contexts must be unique non-empty strings")
        if component_id in component_ids:
            raise ValueError(f"duplicate cyclic component id: {component_id}")
        overlap = claimed_contexts & contexts
        if overlap:
            raise ValueError(f"cyclic component baselines overlap: {', '.join(sorted(overlap))}")
        component_ids.add(component_id)
        claimed_contexts.update(contexts)
        components.append(
            AllowedCyclicComponent(
                component_id=component_id,
                owner=owner,
                reason=reason,
                remove_by=remove_by,
                contexts=contexts,
            )
        )
    return ImportGraphBaseline(
        package=package,
        max_contexts=max_contexts,
        max_cross_context_edges=max_edges,
        max_cyclic_contexts=max_cyclic_contexts,
        allowed_cyclic_components=tuple(components),
    )


def check_import_graph(
    *,
    root: Path = ROOT,
    baseline_path: Path = DEFAULT_BASELINE,
) -> tuple[ImportGraphReport, list[ImportGraphViolation]]:
    baseline = load_baseline(Path(baseline_path))
    report = scan_import_graph(Path(root), package=baseline.package)
    violations: list[ImportGraphViolation] = []

    if len(report.contexts) > baseline.max_contexts:
        violations.append(
            ImportGraphViolation(
                rule="context_budget_exceeded",
                reason=f"runtime contexts increased to {len(report.contexts)}; maximum is {baseline.max_contexts}",
            )
        )
    if len(report.edges) > baseline.max_cross_context_edges:
        violations.append(
            ImportGraphViolation(
                rule="edge_budget_exceeded",
                reason=(
                    f"cross-context edges increased to {len(report.edges)}; "
                    f"maximum is {baseline.max_cross_context_edges}"
                ),
            )
        )
    if report.cyclic_context_count > baseline.max_cyclic_contexts:
        violations.append(
            ImportGraphViolation(
                rule="cyclic_context_budget_exceeded",
                reason=(
                    f"contexts participating in cycles increased to {report.cyclic_context_count}; "
                    f"maximum is {baseline.max_cyclic_contexts}"
                ),
            )
        )

    for evidence in report.non_literal_dynamic_imports:
        violations.append(
            ImportGraphViolation(
                rule="non_literal_dynamic_import",
                reason="non-literal dynamic import hides the runtime dependency target",
                evidence=(evidence,),
            )
        )

    allowed_sets = [component.contexts for component in baseline.allowed_cyclic_components]
    for component in report.cyclic_components:
        actual = frozenset(component)
        if any(actual <= allowed for allowed in allowed_sets):
            continue
        overlaps = [allowed for allowed in allowed_sets if actual & allowed]
        rule = "scc_expanded" if overlaps else "unregistered_scc"
        reason = (
            f"cyclic component [{', '.join(component)}] expands an existing registered SCC"
            if overlaps
            else f"cyclic component [{', '.join(component)}] is not registered"
        )
        violations.append(
            ImportGraphViolation(
                rule=rule,
                reason=reason,
                evidence=_component_evidence(report.edges, actual),
            )
        )

    violations.sort(key=lambda item: (item.rule, item.reason, item.evidence))
    return report, violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the AI-CRM runtime context import graph and SCC budget.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        report, violations = check_import_graph(
            root=Path(args.root).resolve(),
            baseline_path=Path(args.baseline).resolve(),
        )
    except (OSError, ValueError) as exc:
        print(f"Import graph check failed: {exc}")
        return 2

    if args.as_json:
        print(
            json.dumps(
                {
                    "ok": not violations,
                    "report": report.to_dict(),
                    "violations": [item.to_dict() for item in violations],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            "Import graph: "
            f"contexts={len(report.contexts)} "
            f"edges={len(report.edges)} "
            f"cyclic_components={len(report.cyclic_components)} "
            f"cyclic_contexts={report.cyclic_context_count}"
        )
        for index, component in enumerate(report.cyclic_components, start=1):
            print(f"- SCC {index} size={len(component)}: {', '.join(component)}")
        if violations:
            print("Import graph violations:")
            for violation in violations:
                print(f"- {violation.format()}")
        else:
            print("Import graph check OK")
    return 1 if violations else 0


def _iter_python_files(package_dir: Path) -> Iterable[Path]:
    return (
        path
        for path in sorted(package_dir.rglob("*.py"))
        if "__pycache__" not in path.parts and path.is_file()
    )


def _iter_runtime_nodes(node: ast.AST) -> Iterable[ast.AST]:
    if isinstance(node, ast.If) and _is_type_checking_test(node.test):
        yield from _iter_runtime_nodes(node.test)
        for child in node.orelse:
            yield from _iter_runtime_nodes(child)
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _iter_runtime_nodes(child)


def _is_type_checking_test(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _imported_modules(
    node: ast.AST,
    *,
    path: Path,
    package_dir: Path,
    package: str,
) -> list[tuple[str, str]]:
    if isinstance(node, ast.Import):
        return [(alias.name, "import") for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []

    if node.level:
        rel = path.relative_to(package_dir).with_suffix("")
        module_parts = [package, *rel.parts]
        if path.name == "__init__.py":
            module_parts = module_parts[:-1]
            package_parts = module_parts
        else:
            package_parts = module_parts[:-1]
        keep = len(package_parts) - (node.level - 1)
        if keep < 1:
            return []
        base_parts = package_parts[:keep]
        if node.module:
            base_parts.extend(node.module.split("."))
        base = ".".join(base_parts)
    else:
        base = node.module or ""

    modules = [(base, "from_import")] if base else []
    if base == package:
        modules.extend((f"{package}.{alias.name}", "from_import_alias") for alias in node.names if alias.name != "*")
    return modules


def _dynamic_import_aliases(nodes: Iterable[ast.AST]) -> tuple[frozenset[str], frozenset[str]]:
    importlib_aliases = {"importlib"}
    import_module_aliases = {"__import__"}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_aliases.add(alias.asname or alias.name)
    return frozenset(importlib_aliases), frozenset(import_module_aliases)


def _dynamic_import(
    node: ast.AST,
    *,
    importlib_aliases: frozenset[str],
    import_module_aliases: frozenset[str],
) -> tuple[str, str, bool] | None:
    if not isinstance(node, ast.Call):
        return None
    function = node.func
    kind = ""
    if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
        if function.value.id in importlib_aliases and function.attr == "import_module":
            kind = "importlib.import_module"
    elif isinstance(function, ast.Name) and function.id in import_module_aliases:
        kind = function.id
    if not kind:
        return None
    if not node.args:
        return "<missing>", kind, True
    target = node.args[0]
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        module_name = target.value.strip()
        if module_name.startswith("."):
            # Relative dynamic imports require a runtime package argument and are deliberately fail closed.
            return module_name or "<relative>", kind, True
        return module_name, kind, False
    return "<non-literal>", kind, True


def _target_context(module_name: str, package: str) -> str | None:
    parts = str(module_name or "").split(".")
    if len(parts) < 2 or parts[0] != package:
        return None
    return _context_from_package_parts(parts[1:])


def _source_context(relative_parts: tuple[str, ...]) -> str | None:
    if len(relative_parts) < 2:
        return None
    return _context_from_package_parts(relative_parts[:-1])


def _context_from_package_parts(parts: tuple[str, ...] | list[str]) -> str | None:
    normalized = tuple(str(part or "").strip() for part in parts)
    if not normalized or not normalized[0]:
        return None
    if normalized[0] == "extensions":
        return normalized[2] if len(normalized) >= 3 and normalized[2] else None
    if normalized[0] in STABLE_DOMAIN_PACKAGES:
        return normalized[1] if len(normalized) >= 2 and normalized[1] else None
    return normalized[0]


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in sorted(graph.get(node, set())):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            current = stack.pop()
            on_stack.remove(current)
            component.append(current)
            if current == node:
                break
        components.append(component)

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return components


def _component_evidence(
    edges: tuple[ContextImportEdge, ...],
    component: frozenset[str],
) -> tuple[ImportEvidence, ...]:
    return tuple(
        sorted(
            evidence
            for edge in edges
            if edge.source_context in component and edge.target_context in component
            for evidence in edge.evidence
        )
    )


def _required_text(mapping: dict[str, Any], key: str, *, location: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"{location}.{key} is required")
    return value


def _non_negative_int(mapping: dict[str, Any], key: str, *, location: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{location}.{key} must be a non-negative integer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
