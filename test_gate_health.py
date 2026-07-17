import os
import unittest
from unittest.mock import patch

from gate_board import (
    baseline_reasons,
    board_coverage,
    sofa_reasons,
    status_reasons,
)


def game(idx, houses=("A", "B"), market="Faltas", sofa=True, value=False):
    return {
        "jogo": f"H{idx} - A{idx}",
        "sofa_id": idx if sofa else None,
        "mercados": {market: {h: [{"linha": 25.5}] for h in houses}},
        "valor": [{"casa": houses[0]}] if value else [],
    }


class GateHealthTest(unittest.TestCase):
    def test_coverage_counts_per_house_market_and_value_fixture(self):
        cov = board_coverage({"jogos": [
            game(1, market="Faltas", sofa=True, value=True),
            game(2, houses=("A",), market="Cartões", sofa=False, value=True),
        ]})
        self.assertEqual({"A": 2, "B": 1}, cov["houses"])
        self.assertEqual({"Faltas": 1, "Cartões": 1}, cov["markets"])
        self.assertEqual(50, cov["value_sofa_pct"])

    def test_baseline_blocks_house_and_market_collapse(self):
        before = board_coverage({"jogos": [game(i) for i in range(10)]})
        after = board_coverage({"jogos": [game(i, houses=("A",)) for i in range(3)]})
        with patch.dict(os.environ, {
            "GATE_HOUSE_MIN_RATIO": "0.5", "GATE_MARKET_MIN_RATIO": "0.5",
            "GATE_HOUSE_BASE_MIN": "5", "GATE_MARKET_BASE_MIN": "5",
        }):
            reasons = baseline_reasons(after, before)
        self.assertTrue(any("casa B" in x for x in reasons))
        self.assertTrue(any("mercado Faltas" in x for x in reasons))

    def test_baseline_blocks_specific_house_market_collapse(self):
        before_games = []
        after_games = []
        for idx in range(6):
            base = game(idx)
            base["mercados"] = {
                "Faltas": {"A": [1], "B": [1]},
                "Cartões": {"A": [1], "B": [1]},
            }
            before_games.append(base)
            current = game(idx)
            current["mercados"] = {
                "Faltas": {"A": [1]},
                "Cartões": {"A": [1], "B": [1]},
            }
            after_games.append(current)
        before = board_coverage({"jogos": before_games})
        after = board_coverage({"jogos": after_games})
        with patch.dict(os.environ, {
            "GATE_HOUSE_BASE_MIN": "99", "GATE_MARKET_BASE_MIN": "99",
            "GATE_HOUSE_MARKET_BASE_MIN": "5", "GATE_HOUSE_MARKET_MIN_RATIO": "0.5",
        }):
            reasons = baseline_reasons(after, before)
        self.assertEqual(1, len(reasons))
        self.assertIn("B/Faltas", reasons[0])
    def test_sofa_blocks_stale_pointer_and_low_value_coverage(self):
        cov = board_coverage({"jogos": [
            game(i, sofa=i == 0, value=True) for i in range(4)
        ]})
        sofa = {"pointer_valid": True, "pointer_age_h": 15, "pointer_at": "x"}
        with patch.dict(os.environ, {
            "SOFA_GATE_MAX_AGE_H": "12", "SOFA_GATE_BOARD_MIN_PCT": "0",
            "SOFA_GATE_VALUE_MIN_PCT": "70",
        }):
            reasons = sofa_reasons(cov, sofa)
        self.assertTrue(any("defasado" in x for x in reasons))
        self.assertTrue(any("jogos com valor" in x for x in reasons))

    def test_status_requires_markets_and_valid_pointer(self):
        reasons = status_reasons({"deploy_allowed": True, "per_casa": {
            "book": {"ok": True, "n_events": 8, "n_markets": 0,
                     "pointer_valid": False}
        }})
        self.assertEqual(2, len(reasons))


if __name__ == "__main__":
    unittest.main()
