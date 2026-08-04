#!/usr/bin/env python3
"""Create the one supported local Python 3.10 test environment."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
LOCK = ROOT / "requirements.lock"
STAMP = VENV / ".aicrm-requirements-lock.sha256"


def _lock_digest() -> str:
    return hashlib.sha256(LOCK.read_bytes()).hexdigest()


def main() -> int:
    if sys.version_info[:2] != (3, 10):
        print("bootstrap-test requires /usr/local/bin/python3.10", file=sys.stderr)
        return 2
    venv_python = VENV / "bin" / "python"
    if not venv_python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], cwd=ROOT, check=True)
    expected = _lock_digest()
    installed = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
    if installed != expected:
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--require-hashes", "-r", str(LOCK)],
            cwd=ROOT,
            check=True,
        )
        STAMP.write_text(expected + "\n", encoding="utf-8")
    completed = subprocess.run(
        [str(venv_python), "-c", "import sys; assert sys.version_info[:2] == (3, 10)"],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode:
        return completed.returncode
    print(f"test environment ready: {venv_python} ({expected[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
