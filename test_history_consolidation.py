# -*- coding: utf-8 -*-
"""Regressões da consolidação mensal e da precedência de resultados."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from history_settle import (
    consolidate_key_documents,
    find_result,
    persist_consolidated_documents,
)
from migrate_history_keys import migrate_file, migrate_tick_file


def fixture():
    start = datetime(2026, 7, 17, 22, 0, tzinfo=timezone.utc)
    return {
        "sofa_id": 999,
        "home": "Time Alpha",
        "away": "Time Beta",
        "_hn": "time alpha",
        "_an": "time beta",
        "day_brt": "2026-07-17",
        "start_ts": int(start.timestamp()),
        "time_brt": "19:00",
        "league": "Teste",
        "_lfp": "teste",
    }


def record(status="closed"):
    return {
        "status": status,
        "open_odd": 2.1,
        "open_ts": "2026-07-17T09:00:00-03:00",
        "last_odd": 1.9,
        "last_ts": "2026-07-17T18:55:00-03:00",
        "close_odd": 1.9,
        "close_ts": "2026-07-17T18:55:00-03:00",
        "kickoff": "2026-07-17T19:00:00-03:00",
        "home_raw": "Time Alpha",
        "away_raw": "Time Beta",
        "home_norm": "time alpha",
        "away_norm": "time beta",
        "n_obs": 2,
        "n_moves": 1,
    }


class HistoryConsolidationTests(unittest.TestCase):
    def test_manual_result_overrides_auto_when_both_have_field(self):
        auto = {
            "date": "2026-07-17", "_h": "time alpha", "_a": "time beta",
            "corners": 10, "_source": "auto",
        }
        manual = dict(auto, corners=12, _source="manual")
        chosen = find_result(
            [auto, manual], "2026-07-17", "time alpha", "time beta", "corners"
        )
        self.assertIs(chosen, manual)

    def test_monthly_duplicates_merge_once_into_latest_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            july, august = root / "2026-07.json", root / "2026-08.json"
            key = "betano|sofa:999|Escanteios|9.5|over"
            old = record("settled")
            old.update({
                "open_ts": "2026-07-31T18:00:00-03:00",
                "result": 12,
                "won": True,
                "n_obs": 3,
                "n_moves": 1,
            })
            recent = record("closed")
            recent.update({
                "open_ts": "2026-08-01T08:00:00-03:00",
                "close_ts": "2026-08-01T11:58:00-03:00",
                "n_obs": 2,
                "n_moves": 2,
            })
            july_data = {key: old}
            august_data = {"__main_lines__": {}, key: recent}
            july.write_text(json.dumps(july_data), encoding="utf-8")
            august.write_text(json.dumps(august_data), encoding="utf-8")
            docs = [(july, july_data), (august, august_data)]

            merged, owners, duplicates = consolidate_key_documents(docs)
            self.assertEqual(1, duplicates)
            self.assertEqual(august, owners[key])
            self.assertEqual(old["open_ts"], merged[key]["open_ts"])
            self.assertEqual(recent["close_ts"], merged[key]["close_ts"])
            self.assertEqual("settled", merged[key]["status"])
            self.assertEqual(12, merged[key]["result"])
            self.assertEqual(5, merged[key]["n_obs"])
            self.assertEqual(3, merged[key]["n_moves"])

            self.assertEqual(2, persist_consolidated_documents(docs, merged, owners))
            self.assertNotIn(key, json.loads(july.read_text(encoding="utf-8")))
            latest = json.loads(august.read_text(encoding="utf-8"))
            self.assertEqual(old["open_ts"], latest[key]["open_ts"])

            docs2 = [
                (july, json.loads(july.read_text(encoding="utf-8"))),
                (august, latest),
            ]
            merged2, owners2, duplicates2 = consolidate_key_documents(docs2)
            self.assertEqual(0, duplicates2)
            self.assertEqual(5, merged2[key]["n_obs"])
            self.assertEqual(0, persist_consolidated_documents(docs2, merged2, owners2))

    def test_migration_is_idempotent_without_persistent_backup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            key_path = root / "2026-07.json"
            tick_path = root / "2026-07-17.jsonl"
            legacy_key = (
                "betano|2026-07-17|time alpha|time beta|Escanteios|9.5|over"
            )
            key_path.write_text(json.dumps({legacy_key: record()}), encoding="utf-8")
            tick = {
                "ts": "2026-07-17T10:00:00-03:00",
                "kind": "open",
                "casa": "betano",
                "gid": "2026-07-17|time alpha|time beta",
                "kickoff": "2026-07-17T19:00:00-03:00",
                "home": "time alpha",
                "away": "time beta",
                "mercado": "Escanteios",
                "linha": 9.5,
                "lado": "over",
                "odd": 2.1,
                "djogo": "2026-07-17",
            }
            tick_path.write_text(json.dumps(tick) + "\n", encoding="utf-8")
            key_backup = key_path.with_suffix(key_path.suffix + ".bak_pre_migrate")
            tick_backup = tick_path.with_suffix(tick_path.suffix + ".bak_pre_migrate")
            key_backup.write_text("legado", encoding="utf-8")
            tick_backup.write_text("legado", encoding="utf-8")

            migrate_file(key_path, [fixture()])
            migrate_tick_file(tick_path, [fixture()])
            first_key = key_path.read_text(encoding="utf-8")
            first_tick = tick_path.read_text(encoding="utf-8")
            self.assertFalse(key_backup.exists())
            self.assertFalse(tick_backup.exists())
            self.assertIn("sofa:999", first_key)
            self.assertIn("sofa:999", first_tick)

            migrate_file(key_path, [fixture()])
            migrate_tick_file(tick_path, [fixture()])
            self.assertEqual(first_key, key_path.read_text(encoding="utf-8"))
            self.assertEqual(first_tick, tick_path.read_text(encoding="utf-8"))
            self.assertFalse(list(root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
