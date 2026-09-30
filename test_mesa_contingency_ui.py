"""Execute the real contingency JavaScript renderers, offline, with a controlled clock."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_contingency_ui_clocks_filters_and_no_value_signals():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable: cannot execute real frontend renderers")
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [node, str(root / "test_mesa_contingency_ui.js")],
        cwd=root, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["contingencyUiPassed"] is True
    assert evidence["checks"] >= 20
