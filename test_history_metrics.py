import unittest
from datetime import datetime, timedelta
from pathlib import Path

from build_history import LIMIARES, line_key, roi_stats, select_main_signals
from build_moves import is_prematch
from candidate_pricer import CardsPricer, CornersPricer, FoulsPricer, ShotsPricer
from history_quality import BRT, is_strict_clv, strict_clv_reason


def settled(**changes):
    kickoff = datetime(2026, 7, 17, 20, 0, tzinfo=BRT)
    row = {
        "status": "settled",
        "open_odd": 1.95,
        "close_odd": 1.85,
        "open_ts": (kickoff - timedelta(hours=4)).isoformat(),
        "close_ts": (kickoff - timedelta(minutes=2)).isoformat(),
        "kickoff": kickoff.isoformat(),
        "capture_quality": "full_prematch",
        "won": None,
        "result": 10,
    }
    row.update(changes)
    return row


class StrictClvTests(unittest.TestCase):
    def test_push_is_valid_clv(self):
        self.assertTrue(is_strict_clv(settled(won=None)))

    def test_close_at_or_after_kickoff_is_rejected(self):
        row = settled()
        row["close_ts"] = row["kickoff"]
        self.assertFalse(is_strict_clv(row))
        self.assertEqual(strict_clv_reason(row), "close_not_prematch")

    def test_invalid_decimal_odd_is_rejected(self):
        self.assertEqual(strict_clv_reason(settled(open_odd=float("nan"))), "invalid_open")
        self.assertEqual(strict_clv_reason(settled(close_odd=1.0)), "invalid_close")


class SignalMetricTests(unittest.TestCase):
    def _row(self, line, side, house, odd):
        return {
            "gid": "sofa:1", "mercado": "Faltas", "linha": line,
            "lado": side, "casa": house, "open_odd": odd,
            "close_odd": 1.9, "clv_pct": 0.0, "won": True,
            "push": False, "open_epoch": 1,
        }

    def test_selection_is_one_row_per_game_market_and_uses_balanced_main(self):
        rows = [
            self._row(9.5, "over", "a", 1.50),
            self._row(9.5, "under", "a", 2.60),
            self._row(10.5, "over", "a", 1.90),
            self._row(10.5, "under", "a", 1.90),
            self._row(10.5, "over", "b", 1.95),
            self._row(10.5, "under", "b", 1.97),
        ]
        picked = select_main_signals(rows)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["linha"], 10.5)
        self.assertEqual(picked[0]["lado"], "under")
        self.assertEqual(picked[0]["casa"], "b")

    def test_roi_counts_push_stake_with_zero_profit(self):
        rows = []
        for i in range(LIMIARES["roi"]):
            if i == 0:
                won, push = None, True
            elif i <= 25:
                won, push = True, False
            else:
                won, push = False, False
            rows.append({"open_odd": 2.0, "won": won, "push": push})
        stats = roi_stats(rows, "open_odd")
        self.assertEqual(stats["n"], 50)
        self.assertEqual(stats["n_push"], 1)
        self.assertAlmostEqual(stats["roi"], 2.0)

    def test_line_key_matches_javascript_number_string(self):
        self.assertEqual(line_key(5.0), "5")
        self.assertEqual(line_key("5.5"), "5.5")


class MoveTests(unittest.TestCase):
    def test_post_kickoff_and_close_epsilon_are_rejected(self):
        ko = datetime(2026, 7, 17, 20, 0, tzinfo=BRT)
        self.assertTrue(is_prematch(ko - timedelta(minutes=1), ko))
        self.assertFalse(is_prematch(ko - timedelta(seconds=30), ko))
        self.assertFalse(is_prematch(ko + timedelta(seconds=1), ko))


class CandidateMeanTests(unittest.TestCase):
    def test_calibrated_mean_is_the_mean_used_by_price(self):
        tested = 0
        for cls in (CardsPricer, ShotsPricer, FoulsPricer, CornersPricer):
            pricer = cls()
            if not pricer.ok or not pricer.pairs:
                continue
            comp = sorted(pricer.pairs)[0]
            pair = next(iter(pricer.pairs[comp]))
            home, away = map(int, pair.split("|"))
            league = next((raw for raw, mapped in pricer.xwalk.items() if mapped == comp), comp)
            priced = pricer.price(league, home, away, 10.5)
            self.assertIsNotNone(priced)
            self.assertAlmostEqual(priced["mu"], priced["mu_cal"])
            expected = max(0.1, pricer.a + pricer.b * priced["mu_raw"])
            self.assertAlmostEqual(priced["mu_cal"], expected)
            tested += 1
        self.assertGreater(tested, 0)


class FrontendFailClosedTests(unittest.TestCase):
    def test_missing_freshness_is_fail_closed(self):
        valor = Path("valor/js/valor.js").read_text(encoding="utf-8")
        board = Path("valor/js/board.js").read_text(encoding="utf-8")
        self.assertIn("boardAge == null || boardAge > 120", valor)
        self.assertIn('stale: true, band: "unk"', board)
        self.assertIn("return j.sofa_id ? score : Math.min(score, 69)", valor)


if __name__ == "__main__":
    unittest.main()
