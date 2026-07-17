import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import capture_common as cc


class PointerStaleTest(unittest.TestCase):
    def test_full_between_two_and_twelve_hours_is_inventory_only(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            odds = Path(td) / "odds"
            odds.mkdir(parents=True)
            src = odds / "_snapshots" / "book_latest_full.jsonl"
            src.parent.mkdir()
            src.write_text(json.dumps({"mercados": {"Faltas": [1]}}) + "\n", encoding="utf-8")
            at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
            (odds / "book_latest_full.json").write_text(
                json.dumps({"file": "_snapshots/book_latest_full.jsonl", "n": 1,
                            "at": at, "mode": "full"}), encoding="utf-8"
            )
            with patch.multiple(cc, ODDS_DIR=odds, FULL_SNAPSHOT_DIR=odds / "_snapshots"):
                meta, resolved = cc.resolve_odds_pointer("book", prefer_full=True, max_age_h=12)
            self.assertEqual(src, resolved)
            self.assertTrue(meta["_stale"])


if __name__ == "__main__":
    unittest.main()
