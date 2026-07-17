import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import capture_common as cc


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class CaptureResilienceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        self.odds = self.root / "odds"
        self.status = self.odds / "_status"
        self.stack = patch.multiple(
            cc,
            ODDS_DIR=self.odds,
            FULL_SNAPSHOT_DIR=self.odds / "_snapshots",
            STATUS_DIR=self.status,
        )
        self.stack.start()

    def tearDown(self):
        self.stack.stop()
        self.tmp.cleanup()

    def test_full_promotes_stable_copy_and_partial_keeps_it(self):
        src = self.odds / "book_2026-07-17_0100.jsonl"
        rows = [
            {"mercados": {"Faltas": [{"linha": 25.5}]}},
            {"mercados": {"Cartões": [{"linha": 4.5}]}},
        ]
        write_jsonl(src, rows)
        cc.write_odds_latest(
            "book", src.name, 2, at="2026-07-17T01:00:00-03:00", min_events=2
        )
        full_before = json.loads((self.odds / "book_latest_full.json").read_text())
        stable = self.odds / full_before["file"]
        self.assertTrue(stable.is_file())
        self.assertEqual(2, cc._jsonl_count(stable))

        partial = self.odds / "book_2026-07-17_0200.jsonl"
        write_jsonl(partial, [rows[0]])
        cc.write_odds_latest(
            "book", partial.name, 1, at="2026-07-17T02:00:00-03:00", min_events=2
        )
        self.assertEqual(
            full_before,
            json.loads((self.odds / "book_latest_full.json").read_text()),
        )
        self.assertEqual(2, cc._jsonl_count(stable))

    def test_interruption_before_pointer_swap_preserves_previous_full(self):
        first = self.odds / "book_first.jsonl"
        write_jsonl(first, [{"mercados": {"Faltas": [1]}}] * 2)
        cc.write_odds_latest("book", first.name, 2, min_events=1)
        pointer_path = self.odds / "book_latest_full.json"
        before = json.loads(pointer_path.read_text(encoding="utf-8"))
        before_target = self.odds / before["file"]

        second = self.odds / "book_second.jsonl"
        write_jsonl(second, [{"mercados": {"Faltas": [1]}}] * 3)
        real_atomic = cc._atomic_write_text

        def fail_at_pointer(path, text):
            if Path(path).name == "book_latest_full.json":
                raise RuntimeError("interrupção simulada")
            return real_atomic(path, text)

        with patch.object(cc, "_atomic_write_text", side_effect=fail_at_pointer):
            with self.assertRaises(RuntimeError):
                cc.write_odds_latest("book", second.name, 3, min_events=1)

        self.assertEqual(before, json.loads(pointer_path.read_text(encoding="utf-8")))
        self.assertEqual(2, cc._jsonl_count(before_target))

    def test_market_collapse_does_not_replace_healthy_full(self):
        first = self.odds / "book_first.jsonl"
        old_rows = [{"mercados": {"Faltas": [1], "Cartões": [1]}} for _ in range(10)]
        write_jsonl(first, old_rows)
        cc.write_odds_latest("book", first.name, 10, min_events=1)
        pointer_path = self.odds / "book_latest_full.json"
        before = json.loads(pointer_path.read_text(encoding="utf-8"))

        second = self.odds / "book_second.jsonl"
        write_jsonl(second, [{"mercados": {"Faltas": [1]}} for _ in range(10)])
        cc.write_odds_latest("book", second.name, 10, min_events=1)

        self.assertEqual(before, json.loads(pointer_path.read_text(encoding="utf-8")))
        latest = json.loads((self.odds / "book_latest.json").read_text(encoding="utf-8"))
        self.assertTrue(latest.get("promotion_blocked"))
        self.assertEqual(2, cc.finish("book", 10, 1))
        status = json.loads((self.status / "book.json").read_text(encoding="utf-8"))
        self.assertFalse(status["ok"])
        self.assertIn("promoção full bloqueada", status["error"])
    def test_missing_or_truncated_target_is_never_resolved(self):
        self.odds.mkdir(parents=True)
        (self.odds / "book_latest_full.json").write_text(
            json.dumps({
                "file": "_snapshots/missing.jsonl", "n": 10,
                "at": "2026-07-17T02:00:00-03:00", "mode": "full",
            }), encoding="utf-8"
        )
        self.assertEqual((None, None), cc.resolve_odds_pointer("book", prefer_full=True))

        bad = self.odds / "bad.jsonl"
        bad.write_text('{"mercados": {}}\n{quebrado', encoding="utf-8")
        (self.odds / "book_latest.json").write_text(
            json.dumps({"file": bad.name, "n": 2, "at": "2026-07-17T02:00:00-03:00"}),
            encoding="utf-8",
        )
        self.assertEqual((None, None), cc.resolve_odds_pointer("book", prefer_full=False))

    def test_unknown_timestamp_fails_freshness_check(self):
        src = self.odds / "book.jsonl"
        write_jsonl(src, [{"mercados": {"Faltas": []}}])
        self.odds.mkdir(parents=True, exist_ok=True)
        (self.odds / "book_latest.json").write_text(
            json.dumps({"file": src.name, "n": 1, "at": "sem-data"}), encoding="utf-8"
        )
        self.assertEqual(
            (None, None),
            cc.resolve_odds_pointer("book", prefer_full=False, max_age_h=12),
        )

    def test_market_counts_support_normalized_and_betano(self):
        normalized = self.odds / "normal.jsonl"
        write_jsonl(normalized, [
            {"mercados": {"Faltas": [1], "Cartões": [1]}},
            {"mercados": {"Faltas": [1]}, "mercados_time": {"Desarmes": {}}},
        ])
        self.assertEqual(
            {"Cartões": 1, "Desarmes": 1, "Faltas": 2},
            cc.snapshot_market_counts(normalized),
        )
        betano = self.odds / "betano.jsonl"
        write_jsonl(betano, [{"markets": {"estatisticas": [
            {"market": "Total de Faltas"}, {"market": "Time A Total de chutes"}
        ], "cartoes": [{"market": "Total de Cartões"}]}}])
        self.assertEqual(
            {"Cartões": 1, "Faltas": 1, "Finalizações": 1},
            cc.snapshot_market_counts(betano, casa="betano"),
        )

    def test_finish_populates_n_markets_and_pointer_health(self):
        src = self.odds / "book.jsonl"
        write_jsonl(src, [{"mercados": {"Faltas": [1], "Cartões": [1]}}])
        cc.write_odds_latest("book", src.name, 1, min_events=1)
        self.assertEqual(0, cc.finish("book", 1, 1))
        st = json.loads((self.status / "book.json").read_text(encoding="utf-8"))
        self.assertTrue(st["pointer_valid"])
        self.assertEqual(2, st["n_markets"])
        self.assertEqual({"Cartões": 1, "Faltas": 1}, st["market_counts"])


if __name__ == "__main__":
    unittest.main()
