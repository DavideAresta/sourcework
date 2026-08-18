"""Cloud test environment: the package under test is a sibling of core, not an
installed wheel, so put its ``src`` on the path. Core itself is expected to be
installed in this venv (``make install``), which is the monorepo arrangement."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
