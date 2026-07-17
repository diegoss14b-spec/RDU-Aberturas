import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import fetch_fixtures_sofascore as ff
import gate_board as gb
from build_ops import parse_ts_brt


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.payload = payload or {}
        self.text = json.dumps(self.payload) if payload is not None else "blocked"

    def json(self):
        return self.payload


class FixtureHealthTest(unittest.TestCase):
    def test_pointer_target_count_and_age_are_observable(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            root = Path(td)
            out = root / "data" / "fixtures"
            status = root / "data" / "odds" / "_status"
            out.mkdir(parents=True)
            at = datetime.now(timezone.utc).astimezone(ff.BRT).isoformat(timespec="seconds")
            (out / ff.STABLE_FILE).write_text(
                json.dumps({"fixtures": [{"sofa_id": 1}, {"sofa_id": 2}]}),
                encoding="utf-8",
            )
            (out / "sofa_latest.json").write_text(
                json.dumps({"file": ff.STABLE_FILE, "n": 2, "at": at}),
                encoding="utf-8",
            )
            with patch.multiple(ff, OUT=out, STATUS_DIR=status):
                meta, src, n, valid, age = ff.pointer_info()
                self.assertTrue(valid)
                self.assertEqual(2, n)
                self.assertEqual(ff.STABLE_FILE, meta["file"])
                self.assertLess(age, 0.1)
                st = ff.write_status(True, 2, 1, True)
                self.assertTrue(st["pointer_valid"])
                self.assertEqual(2, st["pointer_n"])

    def test_interruption_before_sofa_pointer_swap_preserves_previous(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            root = Path(td)
            out = root / "data" / "fixtures"
            status = root / "data" / "odds" / "_status"
            out.mkdir(parents=True)
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            old_file = out / "stable_old.json"
            old_file.write_text(json.dumps({"fixtures": [{"sofa_id": 1}]}), encoding="utf-8")
            pointer = out / "sofa_latest.json"
            before = {"file": old_file.name, "n": 1, "at": at, "ts_utc": at}
            pointer.write_text(json.dumps(before), encoding="utf-8")
            payload = {
                "at": at,
                "ts_utc": at,
                "fixtures": [{"sofa_id": 2}, {"sofa_id": 3}],
            }
            real_atomic = ff._atomic_write_text

            def fail_at_pointer(path, text):
                if Path(path).name == "sofa_latest.json":
                    raise RuntimeError("interrupção simulada")
                return real_atomic(path, text)

            with patch.multiple(ff, OUT=out, STATUS_DIR=status):
                with patch.object(ff, "_atomic_write_text", side_effect=fail_at_pointer):
                    with self.assertRaises(RuntimeError):
                        ff.promote_fixture_snapshot(payload)
                meta, src, n, valid, _ = ff.pointer_info()
            self.assertTrue(valid)
            self.assertEqual(before, meta)
            self.assertEqual(old_file, src)
            self.assertEqual(1, n)
    def test_gate_can_use_fresh_stable_fallback_after_current_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            root = Path(td)
            fixtures = root / "data" / "fixtures"
            fixtures.mkdir(parents=True)
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            (fixtures / "stable.json").write_text(
                json.dumps({"fixtures": [{"sofa_id": 9}]}), encoding="utf-8"
            )
            (fixtures / "sofa_latest.json").write_text(
                json.dumps({"file": "stable.json", "n": 1, "at": at}), encoding="utf-8"
            )
            with patch.object(gb, "ROOT", root):
                state = gb.load_sofa_state({"ok": False, "error": "timeout"})
            self.assertTrue(state["pointer_valid"])
            self.assertEqual(1, state["pointer_n"])
            self.assertLess(state["pointer_age_h"], 0.1)

    def test_sofa_uses_residential_proxy_first_in_cloud(self):
        ff._reset_get_diag()
        prox = {"https": "http://proxy.invalid"}
        payload = {"seasons": [{"id": 123}]}

        with patch.multiple(ff, PROX=prox):
            with patch.object(ff, "_transport_get", return_value=FakeResponse(200, payload)) as request:
                data = ff.get("https://api.sofascore.com/test", tries=2)

        self.assertEqual(payload, data)
        request.assert_called_once_with("https://api.sofascore.com/test", prox)
        self.assertEqual(1, ff._GET_DIAG["proxy_attempts"])
        self.assertEqual(0, ff._GET_DIAG["direct_attempts"])

    def test_sofa_blocked_proxy_fails_fast_without_direct_route(self):
        ff._reset_get_diag()
        prox = {"https": "http://proxy.invalid"}
        with patch.multiple(ff, PROX=prox):
            with patch.object(ff, "_transport_get", return_value=FakeResponse(403)) as request:
                self.assertIsNone(ff.get("https://api.sofascore.com/test", tries=2))

        request.assert_called_once_with("https://api.sofascore.com/test", prox)
        diag = ff._diag_snapshot()
        self.assertEqual({"403": 1}, diag["statuses"])
        self.assertEqual(0, diag["direct_attempts"])

    def test_sofa_circuit_opens_after_two_exhausted_calls(self):
        ff._reset_get_diag()
        with patch.multiple(ff, PROX=None):
            with patch.object(ff, "_transport_get", side_effect=TimeoutError) as request:
                self.assertIsNone(ff.get("https://api.sofascore.com/a", tries=1))
                self.assertIsNone(ff.get("https://api.sofascore.com/b", tries=1))
                self.assertTrue(ff._GET_DIAG["circuit_open"])
                self.assertIsNone(ff.get("https://api.sofascore.com/c", tries=1))
        self.assertEqual(2, request.call_count)

    def test_sofa_tournament_stops_when_page_covers_window(self):
        ff._reset_get_diag()
        max_ts = 2_000
        events = [
            {"id": 1, "startTimestamp": 1_900},
            {"id": 2, "startTimestamp": 2_100},
        ]
        payload = {"events": events, "hasNextPage": True}
        with patch.object(ff, "season_id", return_value=123):
            with patch.object(ff, "get", return_value=payload) as request:
                with patch.object(ff.time, "sleep", return_value=None):
                    out = ff.fetch_tournament(325, "BR-A", max_ts=max_ts)

        self.assertEqual(events, out)
        request.assert_called_once()

    def test_sofa_tournament_ids_match_expected_leagues(self):
        ids = {label: utid for utid, label in ff.TOURNAMENTS}
        self.assertEqual(155, ids["Argentina"])
        self.assertEqual(649, ids["CSL"])
        self.assertNotIn(648, ids.values())
        self.assertNotIn(136, ids.values())

    def test_ops_parses_iso_and_legacy_brt(self):
        self.assertIsNotNone(parse_ts_brt("2026-07-17 03:10"))
        self.assertIsNotNone(parse_ts_brt("2026-07-17T03:10:00-03:00"))
        self.assertIsNone(parse_ts_brt("sem-data"))


if __name__ == "__main__":
    unittest.main()
