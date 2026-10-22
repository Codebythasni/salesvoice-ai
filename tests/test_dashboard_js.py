"""Runs the JavaScript tests for the dashboard.

The dashboard logic lives in ``static/js/dashboard.js``. It is exercised with
Node and a small DOM stub; see ``tests/js/``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "dashboard_refresh.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_dashboard_survives_a_page_reload():
    result = subprocess.run(
        ["node", "--test", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
