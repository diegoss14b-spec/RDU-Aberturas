"""Wolves is a fixture-proven English Championship alias, never a global one."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from canonical import fixture_scoped_alias_pair, norm_team, parse_start, sofa_purity


class TestIdentityPurityWolves(unittest.TestCase):
    def setUp(self):
        self.sid = "16391676"
        self.kickoff = "2026-09-13T08:00:00-0300"
        self.dt = parse_start(self.kickoff)
        self.fx = {
            "sofa_id": int(self.sid), "home_id": 15, "away_id": 3,
            "home": "Sheffield United", "away": "Wolverhampton",
            "_hn": "sheffield united", "_an": "wolverhampton",
            "league_id": 18, "label": "ENG2", "league": "Championship",
            "day_brt": "2026-09-13", "start_ts": int(self.dt.timestamp()),
        }
        self.raw = {
            "sofa_id": self.sid, "home_raw": "Sheffield United", "away_raw": "Wolves",
            "home_norm": "sheffield united", "away_norm": "wolverhampton",
            "league_raw": "Championship", "kickoff": self.kickoff,
            "match_method": "pair", "match_confidence": 95,
        }
        self.anchor = dict(self.raw, away_raw="Wolverhampton", league_raw="England - Championship")

    def alias(self, record=None, fixtures=None, *, expected_sofa_id="16391676", day=None):
        record = self.raw if record is None else record
        dt = parse_start(record.get("kickoff"))
        return fixture_scoped_alias_pair(
            norm_team(record["home_raw"]), norm_team(record["away_raw"]),
            record.get("league_raw", ""),
            day if day is not None else (dt.strftime("%Y-%m-%d") if dt else "?"), dt,
            [self.fx] if fixtures is None else fixtures, expected_sofa_id=expected_sofa_id,
        )[:2]

    def report(self, record=None, fixtures=None):
        return sofa_purity(
            {"raw": self.raw if record is None else record, "anchor": self.anchor},
            fixtures=[self.fx] if fixtures is None else fixtures,
        )[self.sid]

    def assert_not_aliased(self, record=None, fixtures=None, **kwargs):
        record = self.raw if record is None else record
        self.assertEqual(
            self.alias(record, fixtures, **kwargs),
            (norm_team(record["home_raw"]), norm_team(record["away_raw"])),
        )

    def test_actual_sheffield_wolves_fixture_purifies_only_with_evidence(self):
        self.assertNotEqual(norm_team("Wolves"), norm_team("Wolverhampton"))
        self.assertTrue(self.report(fixtures=[])["impure"])
        self.assertEqual(self.alias(), ("sheffield united", "wolverhampton"))
        report = self.report()
        self.assertFalse(report["impure"])
        self.assertEqual(report["n_clusters"], 1)
        self.assertEqual(report["n_keys"], 2)

    def test_only_reviewed_raw_league_spellings_and_punctuation(self):
        for league in (
            "Championship", "England Championship", "England - Championship",
            "Inglaterra Championship", "Inglaterra - Championship",
            "  ENGLAND: CHAMPIONSHIP  ", "Inglaterra / Championship",
        ):
            with self.subTest(league=league):
                record = dict(self.raw, league_raw=league)
                self.assertEqual(self.alias(record), ("sheffield united", "wolverhampton"))
                self.assertFalse(self.report(record)["impure"])

    def test_generic_other_country_and_category_leagues_do_not_enable_alias(self):
        for league in (
            "", "England", "ENG2", "English Football", "Premier League", "League One",
            "Scotland Championship", "Scottish Championship", "Wales Championship",
            "Northern Ireland Championship", "USL Championship", "Championship Playoffs",
            "England Women's Championship", "England U20 Championship",
            "Inglaterra Campeonato", "AFC Champions League", "UEFA Champions League",
        ):
            with self.subTest(league=league):
                self.assert_not_aliased(dict(self.raw, league_raw=league))

    def test_wolves_as_home_team_with_matching_fixture_orientation(self):
        fixture = dict(self.fx, home="Wolverhampton", away="Sheffield United",
                       home_id=3, away_id=15, _hn="wolverhampton", _an="sheffield united")
        record = dict(self.raw, home_raw="Wolves", away_raw="Sheffield United")
        self.assertEqual(self.alias(record, [fixture]), ("wolverhampton", "sheffield united"))

    def test_reversed_provider_order_preserves_provider_orientation(self):
        record = dict(self.raw, home_raw="Wolves", away_raw="Sheffield United")
        self.assertEqual(self.alias(record), ("wolverhampton", "sheffield united"))
        self.assertFalse(self.report(record)["impure"])

    def test_reversed_fixture_order_also_requires_wolves_id_on_correct_side(self):
        fixture = dict(self.fx, home="Wolverhampton", away="Sheffield United",
                       home_id=3, away_id=15, _hn="wolverhampton", _an="sheffield united")
        self.assertEqual(self.alias(fixtures=[fixture]), ("sheffield united", "wolverhampton"))
        self.assert_not_aliased(fixtures=[dict(fixture, home_id=15, away_id=3)])

    def test_fixture_must_bind_competition_wolves_team_id_and_exact_names(self):
        for changes in (
            {"sofa_id": None}, {"sofa_id": 999}, {"away_id": None}, {"away_id": 999},
            {"home_id": 3, "away_id": 15}, {"home_id": None}, {"home_id": 0},
            {"home_id": -15}, {"home_id": 3},
            {"league_id": 17}, {"league_id": None}, {"label": "ENG1"}, {"label": "SCO2"},
            {"label": ""}, {"_an": "wolves"}, {"_an": "wolverhampton wanderers"},
            {"_an": "wolverhampton women"}, {"_an": "wolverhampton u20"},
            {"_hn": "sheffield wednesday"}, {"_hn": "sheff utd"},
            {"_hn": "sheffield united u20"},
        ):
            with self.subTest(changes=changes):
                self.assert_not_aliased(fixtures=[dict(self.fx, **changes)])

    def test_raw_category_and_homonym_variants_are_not_reviewed_aliases(self):
        for name in (
            "Wolves (Women)", "Wolves Women", "Wolves F", "Wolves U20", "Wolves U21",
            "Wolves B", "Wolves II", "Wolves Reservas", "Wolves Reserves",
            "Wolves Academy", "Wollongong Wolves", "Wolves Youth",
        ):
            with self.subTest(name=name):
                self.assert_not_aliased(dict(self.raw, away_raw=name))

    def test_opponent_must_be_exact_not_fuzzy_abbreviated_or_another_category(self):
        for name in (
            "Sheff Utd", "Sheffield", "Sheffield Wednesday", "Sheffield United U20",
            "Sheffield United Women", "Sheffield United B", "Sheffield United II",
            "Sheffield United Reserves", "Burnley", "Wolves", "Wolverhampton", "",
        ):
            with self.subTest(name=name):
                self.assert_not_aliased(dict(self.raw, home_raw=name))

    def test_a_fuzzy_opponent_must_not_become_valid_because_its_id_matches(self):
        record = dict(self.raw, home_raw="Sheff Utd", home_norm="sheffield united")
        self.assert_not_aliased(record)
        self.assertTrue(self.report(record)["impure"])

    def test_exact_future_opponent_is_not_pinned_to_sheffield_or_its_team_id(self):
        fixture = dict(self.fx, home="Burnley", _hn="burnley", home_id=6)
        record = dict(self.raw, home_raw="Burnley")
        self.assertEqual(self.alias(record, [fixture]), ("burnley", "wolverhampton"))
        # The opposing team's name must match the current authoritative snapshot;
        # this guard deliberately does not introduce another worldwide name/ID map.
        self.assertEqual(self.alias(record, [dict(fixture, home_id=999)]),
                         ("burnley", "wolverhampton"))

    def test_no_fixture_and_no_start_timestamp_mean_no_alias(self):
        self.assert_not_aliased(fixtures=[])
        for stamp in (None, 0, "bad timestamp"):
            with self.subTest(stamp=stamp):
                self.assert_not_aliased(fixtures=[dict(self.fx, start_ts=stamp)])
        for kickoff in (None, "", "bad date"):
            with self.subTest(kickoff=kickoff):
                self.assert_not_aliased(dict(self.raw, kickoff=kickoff))

    def test_45_minute_boundary_is_inclusive_in_both_directions(self):
        for minutes in (-45, 45):
            with self.subTest(minutes=minutes):
                record = dict(self.raw, kickoff=(self.dt + timedelta(minutes=minutes)).isoformat())
                self.assertEqual(self.alias(record), ("sheffield united", "wolverhampton"))
        for seconds in (-2701, 2701):
            with self.subTest(seconds=seconds):
                record = dict(self.raw, kickoff=(self.dt + timedelta(seconds=seconds)).isoformat())
                self.assert_not_aliased(record)

    def test_same_instant_with_another_timezone_is_valid(self):
        self.assertEqual(self.alias(dict(self.raw, kickoff="2026-09-13T11:00:00Z")),
                         ("sheffield united", "wolverhampton"))

    def test_same_civil_day_is_mandatory_not_just_a_small_time_delta(self):
        self.assert_not_aliased(fixtures=[dict(self.fx, day_brt="2026-09-14")])
        self.assert_not_aliased(day="2026-09-14")
        record = dict(self.raw, kickoff="2026-09-12T23:50:00-0300")
        fixture = dict(self.fx, day_brt="2026-09-13",
                       start_ts=int(parse_start("2026-09-13T00:10:00-0300").timestamp()))
        self.assert_not_aliased(record, [fixture])

    def test_multiple_distinct_matching_fixtures_are_ambiguous_even_with_expected_id(self):
        fixtures = [self.fx, dict(self.fx, sofa_id=123)]
        self.assert_not_aliased(fixtures=fixtures)
        self.assertTrue(self.report(fixtures=fixtures)["impure"])

    def test_other_nonmatching_candidates_do_not_make_the_valid_fixture_ambiguous(self):
        fixtures = [dict(self.fx, sofa_id=123, _hn="burnley"), self.fx]
        self.assertEqual(self.alias(fixtures=fixtures), ("sheffield united", "wolverhampton"))

    def test_existing_identity_requires_matching_expected_sofa_id(self):
        self.assert_not_aliased(expected_sofa_id="123")
        self.assert_not_aliased(expected_sofa_id=0)
        self.assertTrue(self.report(fixtures=[dict(self.fx, sofa_id=123)])["impure"])
        fixtures = [dict(self.fx, away_id=999), dict(self.fx, sofa_id=123)]
        self.assertTrue(self.report(fixtures=fixtures)["impure"])

    def test_purity_passes_expected_sofa_id_to_the_alias_guard(self):
        with patch("canonical.fixture_scoped_alias_pair", wraps=fixture_scoped_alias_pair) as guard:
            self.report()
        self.assertEqual(guard.call_count, 2)
        for call in guard.call_args_list:
            self.assertEqual(str(call.kwargs["expected_sofa_id"]), self.sid)

    def test_stored_canonical_names_and_claimed_alias_context_are_not_fixture_evidence(self):
        record = dict(self.raw, league_raw="USL Championship", alias_context="England Championship",
                      home_norm="sheffield united", away_norm="wolverhampton",
                      match_evidence={"method": "pair", "strong": 100})
        self.assert_not_aliased(record)
        self.assertTrue(self.report(record)["impure"])
        self.assertTrue(self.report(fixtures=[])["impure"])

    def test_purity_cache_keeps_league_and_kickoff_context_separate(self):
        for invalid in (
            dict(self.raw, league_raw="Scotland Championship"),
            dict(self.raw, kickoff=(self.dt + timedelta(minutes=46)).isoformat()),
        ):
            with self.subTest(invalid=invalid):
                result = sofa_purity({"valid": self.raw, "anchor": self.anchor, "invalid": invalid},
                                     fixtures=[self.fx])[self.sid]
                self.assertTrue(result["impure"])
                self.assertEqual(result["n_keys"], 3)

    def test_purity_cache_does_not_reuse_proof_for_a_different_sofa_id(self):
        other_sid = "123"
        keys = {"valid": self.raw, "anchor": self.anchor,
                "other_raw": dict(self.raw, sofa_id=other_sid),
                "other_anchor": dict(self.anchor, sofa_id=other_sid)}
        result = sofa_purity(keys, fixtures=[self.fx])
        self.assertFalse(result[self.sid]["impure"])
        self.assertTrue(result[other_sid]["impure"])

    def test_default_purity_loads_current_fixture_evidence(self):
        with patch("canonical.load_sofa_fixtures", return_value=[self.fx]) as load:
            result = sofa_purity({"raw": self.raw, "anchor": self.anchor})
        load.assert_called_once_with()
        self.assertFalse(result[self.sid]["impure"])

    def test_alias_and_purity_do_not_mutate_the_raw_records_or_fixture_evidence(self):
        keys, fixtures = {"raw": self.raw, "anchor": self.anchor}, [self.fx]
        before = deepcopy((keys, fixtures))
        self.alias(fixtures=fixtures)
        sofa_purity(keys, fixtures=fixtures)
        self.assertEqual(sofa_purity(keys, only_ids={"other"}, fixtures=fixtures), {})
        self.assertEqual((keys, fixtures), before)


if __name__ == "__main__":
    unittest.main()
