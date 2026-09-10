"""The entry points the desktop shell relies on.

The Tauri shell starts the backend as ``python -m sourcework app`` because it
knows an interpreter but not where a virtualenv put the console script. That
makes this module part of the shell's contract, so it is pinned here.
"""

from __future__ import annotations

import subprocess
import sys


def test_python_m_sourcework_runs_the_cli():
    result = subprocess.run(
        [sys.executable, "-m", "sourcework", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    # The shell launches the `app` subcommand; the module must route to the
    # same parser the console script uses.
    assert "serve-all" in result.stdout
    assert "app" in result.stdout
