import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import capture_common as cc


def put(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class SameMinuteRetryTest(unittest.TestCase):
    def test_progressive_retry_never_replaces_last_healthy_full(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            odds = Path(td) / "odds"
            with patch.multiple(
                cc, ODDS_DIR=odds, FULL_SNAPSHOT_DIR=odds / "_snapshots",
                STATUS_DIR=odds / "_status",
            ):
                path = odds / "book_2026-07-17_0100.jsonl"
                healthy = [{"mercados": {"Faltas": [1]}}, {"mercados": {"Faltas": [2]}}]
                put(path, healthy)
                cc.write_odds_latest("book", path.name, 2, min_events=2)
                full_ptr = json.loads((odds / "book_latest_full.json").read_text())
                stable = odds / full_ptr["file"]

                # Retry reutiliza o mesmo nome/minuto: enquanto parcial, nao publica.
                put(path, [healthy[0]])
                cc.write_odds_latest("book", path.name, 1, min_events=2)
                self.assertEqual(2, cc._jsonl_count(stable))
                self.assertEqual(full_ptr, json.loads((odds / "book_latest_full.json").read_text()))

                # Somente o arquivo novamente completo troca a copia/pointer atomicos.
                retried = healthy + [{"mercados": {"Cartões": [1]}}]
                put(path, retried)
                cc.write_odds_latest("book", path.name, 3, min_events=2)
                current_ptr = json.loads((odds / "book_latest_full.json").read_text())
                current = odds / current_ptr["file"]
                self.assertEqual(3, cc._jsonl_count(current))
                self.assertEqual(3, current_ptr["n"])
                self.assertEqual(2, cc._jsonl_count(stable))


if __name__ == "__main__":
    unittest.main()
