"""Regression: verified fixture aliases must reach the publication purity gate."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from canonical import fixture_scoped_alias_pair, norm_team, parse_start, sofa_purity


class TestIdentityPurityAliases(unittest.TestCase):
    def setUp(self):
        self.sid = "16938796"
        self.kickoff = "2026-09-09T16:00:00-0300"
        self.dt = parse_start(self.kickoff)
        self.fx = {
            "sofa_id": int(self.sid), "home_id": 1644, "away_id": 2404,
            "home": "Paris Saint-Germain", "away": "ŠK Slovan Bratislava",
            "_hn": "paris saint germain", "_an": "slovan bratislava",
            "league_id": 7, "label": "UCL", "league": "UEFA Champions League",
            "day_brt": "2026-09-09", "start_ts": int(self.dt.timestamp()),
        }
        self.raw = {
            "sofa_id": self.sid, "home_raw": "PSG", "away_raw": "Slovan Bratislava",
            "home_norm": "paris saint germain", "away_norm": "slovan bratislava",
            "league_raw": "Liga dos Campeões", "kickoff": self.kickoff,
            "match_method": "pair", "match_confidence": 95,
        }
        self.anchor = dict(self.raw, home_raw="Paris Saint-Germain", league_raw="UEFA - Champions League")

    def report(self, record=None, fixtures=None):
        return sofa_purity({
            "sportingbet|sofa:16938796|Escanteios|10.5|over": record or self.raw,
            "pinnacle|sofa:16938796|Escanteios|10.5|over": self.anchor,
        }, fixtures=[self.fx] if fixtures is None else fixtures)[self.sid]

    def alias(self, fixture=None, **changes):
        record = dict(self.raw, **changes)
        dt = parse_start(record.get("kickoff"))
        return fixture_scoped_alias_pair(
            norm_team(record["home_raw"]), norm_team(record["away_raw"]),
            record["league_raw"], dt.strftime("%Y-%m-%d") if dt else "?", dt,
            [fixture or self.fx], expected_sofa_id=record["sofa_id"],
        )[:2]

    def test_real_psg_pair_is_pure_only_with_fixture_proof(self):
        self.assertTrue(self.report(fixtures=[])["impure"])
        result = self.report()
        self.assertFalse(result["impure"])
        self.assertEqual(result["n_clusters"], 1)
        self.assertEqual(result["n_keys"], 2)
        self.assertNotEqual(norm_team("PSG"), norm_team("Paris Saint-Germain"))

    def test_default_uses_current_fixture_snapshot(self):
        with patch("canonical.load_sofa_fixtures", return_value=[self.fx]) as load:
            report = sofa_purity({"one": self.raw, "two": self.anchor})
        load.assert_called_once_with()
        self.assertFalse(report[self.sid]["impure"])

    def test_raw_league_must_be_reviewed_not_a_generic_champions_fingerprint(self):
        for league in ("", "Premier League", "AFC Champions League", "CAF Champions League",
                       "UEFA Women's Champions League", "UEFA Youth Champions League"):
            with self.subTest(league=league):
                self.assertTrue(self.report(dict(self.raw, league_raw=league))["impure"])

    def test_observed_league_spellings(self):
        for league in ("Champions League", "UEFA Champions League", "UEFA - Champions League", "Liga dos Campeões"):
            with self.subTest(league=league):
                self.assertFalse(self.report(dict(self.raw, league_raw=league))["impure"])

    def test_missing_late_or_wrong_day_kickoff_does_not_prove_alias(self):
        for kickoff in (None, "bad date", (self.dt + timedelta(minutes=46)).isoformat(),
                        (self.dt + timedelta(days=1)).isoformat()):
            with self.subTest(kickoff=kickoff):
                self.assertTrue(self.report(dict(self.raw, kickoff=kickoff))["impure"])

    def test_kickoff_tolerance_boundary(self):
        record = dict(self.raw, kickoff=(self.dt + timedelta(minutes=45)).isoformat())
        self.assertFalse(self.report(record)["impure"])

    def test_fixture_ids_names_competition_and_orientation_are_bound(self):
        for changes in (
            {"sofa_id": 999}, {"sofa_id": None}, {"home_id": 999}, {"away_id": 999},
            {"home_id": 2404, "away_id": 1644}, {"league_id": 16}, {"label": "CAF"},
            {"_hn": "paris fc"}, {"_an": "lask"}, {"_hn": "paris saint germain f"},
            {"day_brt": "2026-09-10"}, {"start_ts": self.fx["start_ts"] + 7200},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(self.report(fixtures=[dict(self.fx, **changes)])["impure"])

    def test_a_different_fixture_cannot_purify_expected_sofa_id(self):
        fixtures = [dict(self.fx, _hn="paris fc", home_id=999), dict(self.fx, sofa_id=123)]
        self.assertTrue(self.report(fixtures=fixtures)["impure"])

    def test_ambiguous_fixture_candidates_remain_impure(self):
        self.assertTrue(self.report(fixtures=[self.fx, dict(self.fx, sofa_id=123)])["impure"])

    def test_category_homonym_and_opponent_negatives(self):
        for home, away in (
            ("PSG (Women)", "Slovan Bratislava"), ("PSG U20", "Slovan Bratislava"),
            ("PSG B", "Slovan Bratislava"), ("PSG II", "Slovan Bratislava"),
            ("PSG Reservas", "Slovan Bratislava"), ("PSG Academy", "Slovan Bratislava"),
            ("PSG", "Slovan Bratislava U20"), ("PSG", "LASK"),
        ):
            with self.subTest(home=home, away=away):
                self.assertTrue(self.report(dict(self.raw, home_raw=home, away_raw=away))["impure"])

    def test_stored_canonical_names_and_claimed_alias_context_are_not_proof(self):
        forged = dict(self.raw, league_raw="", alias_context="UEFA Champions League",
                      match_evidence={"method": "pair", "strong": 100})
        self.assertTrue(self.report(forged)["impure"])

    def test_same_raw_pair_in_invalid_context_is_not_hidden_by_cache(self):
        keys = {"valid": self.raw, "anchor": self.anchor,
                "wrong_competition": dict(self.raw, league_raw="AFC Champions League")}
        result = sofa_purity(keys, fixtures=[self.fx])[self.sid]
        self.assertTrue(result["impure"])
        self.assertEqual(result["n_keys"], 3)

    def test_reversed_raw_pair_keeps_orientation_and_same_event_semantics(self):
        reversed_record = dict(self.raw, home_raw="Slovan Bratislava", away_raw="PSG")
        self.assertEqual(self.alias(**reversed_record), ("slovan bratislava", "paris saint germain"))
        self.assertFalse(self.report(reversed_record)["impure"])

    def test_country_omitted_alias_uses_same_exact_event_guard(self):
        fx = dict(self.fx, home_id=1684, away_id=1666, _hn="angers", _an="stade rennais",
                  league_id=34, label="Ligue1")
        args = ("angers", "rennes", "Ligue 1", "2026-09-09", self.dt, [fx])
        self.assertEqual(fixture_scoped_alias_pair(*args, expected_sofa_id=self.sid)[:2],
                         ("angers", "stade rennais"))
        self.assertEqual(fixture_scoped_alias_pair(*args, expected_sofa_id=999)[:2],
                         ("angers", "rennes"))

    def test_purity_does_not_mutate_evidence_and_respects_filter(self):
        keys, fixtures = {"one": self.raw, "two": self.anchor}, [self.fx]
        original = deepcopy((keys, fixtures))
        self.assertEqual(sofa_purity(keys, only_ids={"other"}, fixtures=fixtures), {})
        sofa_purity(keys, fixtures=fixtures)
        self.assertEqual((keys, fixtures), original)


if __name__ == "__main__":
    unittest.main()
