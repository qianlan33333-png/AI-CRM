#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_FORMAT_LIMIT = 20_000
GITHUB_HARD_FORMAT_LIMIT = 21_000


@dataclass(frozen=True)
class FormatViolation:
    workflow: Path
    scalar_path: str
    expression_length: int

    def render(self, root: Path) -> str:
        try:
            workflow = self.workflow.relative_to(root)
        except ValueError:
            workflow = self.workflow
        return (
            f"{workflow}:{self.scalar_path}: TemplateReader expression length "
            f"{self.expression_length} exceeds repository limit {REPOSITORY_FORMAT_LIMIT} "
            f"(GitHub hard limit {GITHUB_HARD_FORMAT_LIMIT})"
        )


def _expression_segments(value: str) -> list[tuple[int, int, str]]:
    segments: list[tuple[int, int, str]] = []
    cursor = 0
    while True:
        start = value.find("${{", cursor)
        if start < 0:
            return segments
        index = start + 3
        in_string = False
        while index < len(value) - 1:
            character = value[index]
            if character == "'":
                in_string = not in_string
                index += 1
                continue
            if not in_string and value[index : index + 2] == "}}":
                end = index + 2
                segments.append((start, end, value[start + 3 : index].strip()))
                cursor = end
                break
            index += 1
        else:
            return segments


def simulate_template_reader_format(value: str) -> str | None:
    """Build the BasicExpressionToken GitHub validates for an interpolated scalar."""
    segments = _expression_segments(value)
    if not segments:
        return None
    if len(segments) == 1 and segments[0][0] == 0 and segments[0][1] == len(value):
        return segments[0][2]
    parts: list[str] = []
    arguments: list[str] = []
    cursor = 0
    for index, (start, end, expression) in enumerate(segments):
        literal = value[cursor:start]
        parts.append(
            literal.replace("'", "''").replace("{", "{{").replace("}", "}}")
        )
        parts.append(f"{{{index}}}")
        arguments.append(expression)
        cursor = end
    literal = value[cursor:]
    parts.append(
        literal.replace("'", "''").replace("{", "{{").replace("}", "}}")
    )
    argument_source = "".join(f", {argument}" for argument in arguments)
    return f"format('{''.join(parts)}'{argument_source})"


def utf16_code_units(value: str) -> int:
    """Match .NET String.Length, which GitHub uses for ExpressionConstants.MaxLength."""
    return len(value.encode("utf-16-le")) // 2


def _iter_scalars(node: Node, scalar_path: str = "$") -> Iterator[tuple[str, ScalarNode]]:
    if isinstance(node, ScalarNode):
        yield scalar_path, node
        return
    if isinstance(node, SequenceNode):
        for index, child in enumerate(node.value):
            yield from _iter_scalars(child, f"{scalar_path}[{index}]")
        return
    if isinstance(node, MappingNode):
        for index, (key, child) in enumerate(node.value):
            yield from _iter_scalars(key, f"{scalar_path}.<key:{index}>")
            key_label = key.value if isinstance(key, ScalarNode) else f"item:{index}"
            yield from _iter_scalars(child, f"{scalar_path}.{key_label}")


def check_workflows(root: Path = ROOT) -> list[FormatViolation]:
    if REPOSITORY_FORMAT_LIMIT >= GITHUB_HARD_FORMAT_LIMIT:
        raise RuntimeError("repository expression limit must stay below GitHub's hard limit")
    violations: list[FormatViolation] = []
    workflow_dir = root / ".github" / "workflows"
    for workflow in sorted(workflow_dir.glob("*.y*ml")):
        document = yaml.compose(workflow.read_text(encoding="utf-8"))
        if document is None:
            continue
        for scalar_path, scalar in _iter_scalars(document):
            expression = simulate_template_reader_format(scalar.value)
            expression_length = (
                utf16_code_units(expression) if expression is not None else 0
            )
            if expression is not None and expression_length > REPOSITORY_FORMAT_LIMIT:
                violations.append(
                    FormatViolation(
                        workflow=workflow,
                        scalar_path=scalar_path,
                        expression_length=expression_length,
                    )
                )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject GitHub Actions expression-bearing scalars before TemplateReader's format limit."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    violations = check_workflows(root)
    if violations:
        for violation in violations:
            print(violation.render(root))
        return 1
    print("GitHub Actions expression length check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
