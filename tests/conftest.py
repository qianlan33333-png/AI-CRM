"""Repository-wide pytest bootstrap.

Keep this file deliberately small: it may make the repository importable, but it
must not import the application, connect to PostgreSQL, or install autouse
fixtures. Layer-specific fixtures live below their respective test directories.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
