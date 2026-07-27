from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
AUTO_BASE_SUBJECT_RE = re.compile(r"(AI-CRM-ID-refactor|sync-id-refactor)", re.IGNORECASE)
REPORT_VERSION = "1"


@dataclass(frozen=True)
class NumstatRow:
    added: int
    deleted: int
    path: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report refactor line growth by coarse source category.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--base", default="auto", help="Base ref. Defaults to auto-detecting the recent ID-refactor merge baseline.")
    parser.add_argument("--target", default="HEAD")
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    base = resolve_base_ref(root, args.base, target=args.target)
    report = build_report(root=root, base=base, target=args.target)
    rendered = render_report(report)
    print(rendered)
    if args.json_output:
        output = root / args.json_output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def resolve_base_ref(root: Path = ROOT, base: str = "auto", *, target: str = "HEAD") -> str:
    if base != "auto":
        return base
    detected = _detect_recent_refactor_base(root=root, target=target)
    if detected:
        return detected
    merge_base = _git(root, ["merge-base", "origin/main", target], check=False).strip()
    if merge_base:
        return merge_base
    return f"{target}~1"


def build_report(root: Path = ROOT, *, base: str, target: str = "HEAD") -> dict[str, object]:
    numstat = _git(root, ["diff", "--numstat", f"{base}..{target}"])
    rows = parse_numstat(numstat)
    categories = summarize_rows(rows)
    totals = {
        "added": sum(item["added"] for item in categories.values()),
        "deleted": sum(item["deleted"] for item in categories.values()),
        "net": sum(item["net"] for item in categories.values()),
        "files": sum(item["files"] for item in categories.values()),
    }
    return {
        "version": REPORT_VERSION,
        "base": base,
        "target": target,
        "totals": totals,
        "categories": categories,
    }


def parse_numstat(output: str) -> list[NumstatRow]:
    rows: list[NumstatRow] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3 or "-" in parts[:2]:
            continue
        try:
            added = int(parts[0])
            deleted = int(parts[1])
        except ValueError:
            continue
        rows.append(NumstatRow(added=added, deleted=deleted, path=_normalize_numstat_path(parts[-1])))
    return rows


def summarize_rows(rows: Iterable[NumstatRow]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = defaultdict(lambda: {"added": 0, "deleted": 0, "net": 0, "files": 0})
    for row in rows:
        category = categorize_path(row.path)
        summary[category]["added"] += row.added
        summary[category]["deleted"] += row.deleted
        summary[category]["net"] += row.added - row.deleted
        summary[category]["files"] += 1
    return dict(sorted(summary.items(), key=lambda item: (-item[1]["net"], item[0])))


def categorize_path(path: str) -> str:
    normalized = path.strip().lstrip("./")
    suffix = Path(normalized).suffix.lower()
    if normalized.startswith("migrations/") or normalized == "alembic.ini":
        return "migrations"
    if normalized.startswith("tests/"):
        return "tests"
    if suffix in {".yml", ".yaml"} and (
        normalized.startswith(".github/")
        or normalized.startswith("docs/architecture/")
        or normalized.startswith("docs/development/")
        or normalized.startswith("deploy/")
    ):
        return "manifests/yml"
    if "/templates/" in normalized or normalized.endswith(".html"):
        return "templates/pages"
    if normalized.startswith("tools/check_") or normalized in {
        "tools/audit_repo_hygiene.py",
        "tools/check_architecture_boundaries.py",
        "tools/check_sql_static_guard.py",
    }:
        return "tools/guards"
    if normalized.startswith("tools/"):
        return "tools/other"
    if normalized.startswith("docs/"):
        return "docs"
    if normalized.startswith("aicrm_next/app/admin_console/static/") or "/static/" in normalized:
        return "frontend/static"
    if normalized.startswith("aicrm_next/") and suffix == ".py":
        if any(token in normalized for token in ("/api.py", "/admin_api.py", "/routes.py", "/admin_pages.py", "/pages.py")):
            return "runtime APIs"
        return "runtime/other"
    if normalized.startswith("aicrm_next/"):
        return "runtime/other"
    if normalized.startswith("scripts/"):
        return "scripts"
    if normalized.startswith("deploy/"):
        return "deploy"
    return "other"


def render_report(report: dict[str, object]) -> str:
    totals = report["totals"]
    lines = [
        "# Refactor Line Growth Report",
        "",
        f"Base: `{report['base']}`",
        f"Target: `{report['target']}`",
        "",
        f"Total added: {totals['added']}",
        f"Total deleted: {totals['deleted']}",
        f"Net growth: {totals['net']}",
        f"Files changed: {totals['files']}",
        "",
        "| Category | Added | Deleted | Net | Files |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for category, item in report["categories"].items():
        lines.append(f"| `{category}` | {item['added']} | {item['deleted']} | {item['net']} | {item['files']} |")
    return "\n".join(lines)


def _detect_recent_refactor_base(root: Path, *, target: str) -> str:
    output = _git(root, ["log", "--format=%H%x09%P%x09%s", "--max-count=200", target], check=False)
    matches: list[tuple[str, str]] = []
    for line in output.splitlines():
        commit, parents, subject = (line.split("\t", 2) + ["", ""])[:3]
        if AUTO_BASE_SUBJECT_RE.search(subject):
            first_parent = parents.split()[0] if parents.split() else ""
            if first_parent:
                matches.append((commit, first_parent))
    return matches[-1][1] if matches else ""


def _normalize_numstat_path(path: str) -> str:
    # git numstat can render renames as "a/{old => new}.py"; for reporting, the
    # destination side is the useful category signal.
    if " => " not in path:
        return path
    return path.split(" => ", 1)[1].replace("}", "").strip()


def _git(root: Path, args: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


if __name__ == "__main__":
    raise SystemExit(main())
