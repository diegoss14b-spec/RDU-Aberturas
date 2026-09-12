"""Verify that the real Mesa frontend renders every eligible game, without HTTP."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_mesa_renders_all_eligible_games_without_count_or_future_horizon_cap():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable: cannot execute the real Mesa frontend renderer")
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [node, str(root / "test_mesa_frontend_uncapped.js")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence == {
        "inputGames": 1210,
        "expectedVisible": 1207,
        "actualRendered": 1207,
        "lateGameVisible": True,
        "onlyTeamMarketVisible": True,
        "excludedStartedWrongHouseWrongMarket": True,
    }
