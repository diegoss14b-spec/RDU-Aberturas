"""EFL Cup aliases require event evidence, never a global Wolves replacement."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from canonical import fixture_scoped_alias_pair, norm_team, parse_start, sofa_purity


class TestIdentityPurityWolvesEflc(unittest.TestCase):
    def setUp(self):
        # Source fixture and raw spellings observed on 14 September 2026.
        self.sid = "16992612"
        self.kickoff = "2026-09-16T15:45:00-0300"
        self.dt = parse_start(self.kickoff)
        self.fx = {
            "sofa_id": 16992612, "home_id": 48, "away_id": 3,
            "home": "Everton", "away": "Wolverhampton",
            "_hn": "everton", "_an": "wolverhampton",
            "league_id": 21, "label": "EFLC", "league": "EFL Cup",
            "day_brt": "2026-09-16", "start_ts": int(self.dt.timestamp()),
        }
        self.raw = {
            "sofa_id": self.sid, "home_raw": "Everton", "away_raw": "Wolves",
            "home_norm": "everton", "away_norm": "wolverhampton",
            "league_raw": "Inglaterra EFL Cup", "kickoff": self.kickoff,
            "match_method": "pair", "match_confidence": 95,
        }
        self.anchor = dict(self.raw, away_raw="Wolverhampton", league_raw="England EFL Cup")

    def alias(self, record=None, fixtures=None, *, expected_sofa_id="16992612", day=None):
        record = self.raw if record is None else record
        dt = parse_start(record.get("kickoff"))
        return fixture_scoped_alias_pair(
            norm_team(record["home_raw"]), norm_team(record["away_raw"]),
            record.get("league_raw", ""),
            day if day is not None else (dt.strftime("%Y-%m-%d") if dt else "?"), dt,
            [self.fx] if fixtures is None else fixtures, expected_sofa_id=expected_sofa_id,
        )

    def report(self, record=None, fixtures=None):
        return sofa_purity(
            {"raw": self.raw if record is None else record, "anchor": self.anchor},
            fixtures=[self.fx] if fixtures is None else fixtures,
        )[self.sid]

    def assert_not_aliased(self, record=None, fixtures=None, **kwargs):
        record = self.raw if record is None else record
        self.assertEqual(
            self.alias(record, fixtures, **kwargs)[:2],
            (norm_team(record["home_raw"]), norm_team(record["away_raw"])),
        )

    def test_actual_everton_wolves_purifies_only_with_fixture_evidence(self):
        self.assertNotEqual(norm_team("Wolves"), norm_team("Wolverhampton"))
        self.assertTrue(self.report(fixtures=[])["impure"])
        self.assertEqual(self.alias(), ("everton", "wolverhampton", "England - EFL Cup"))
        result = self.report()
        self.assertFalse(result["impure"])
        self.assertEqual(result["n_clusters"], 1)
        self.assertEqual(result["n_keys"], 2)

    def test_four_observed_house_spellings_form_one_cluster_without_losing_keys(self):
        keys = {
            "7k": self.raw,
            "bet365": self.anchor,
            "estrelabet": dict(self.raw, away_raw="Wolverhampton Wanderers", league_raw="EFL Cup"),
            "sportingbet": dict(self.anchor, league_raw="EFL Cup"),
        }
        result = sofa_purity(keys, fixtures=[self.fx])[self.sid]
        self.assertFalse(result["impure"])
        self.assertEqual(result["n_keys"], 4)
        self.assertEqual(result["n_clusters"], 1)

    def test_only_reviewed_efl_spellings_and_punctuation_enable_proof(self):
        for league in (
            "EFL Cup", "England EFL Cup", "English EFL Cup", "Inglaterra EFL Cup",
            "England - EFL Cup", "Inglaterra - EFL Cup", "English / EFL Cup",
            "  ENGLAND: EFL CUP  ",
        ):
            with self.subTest(league=league):
                record = dict(self.raw, league_raw=league)
                self.assertEqual(self.alias(record), ("everton", "wolverhampton", "England - EFL Cup"))
                self.assertFalse(self.report(record)["impure"])

    def test_nonreviewed_countries_competitions_and_categories_do_not_enable_proof(self):
        for league in (
            "", "England", "EFLC", "English Football", "Carabao Cup", "League Cup",
            "Copa da Liga", "Inglaterra Copa da Liga", "FA Cup", "Premier League",
            "Championship", "England Championship", "Inglaterra Championship",
            "Scotland EFL Cup", "Wales EFL Cup", "Northern Ireland EFL Cup", "USL Cup",
            "EFL Cup Women", "England Women's EFL Cup", "EFL Cup U21", "England EFL Trophy",
            "EFL Cup Qualifying", "England EFL Cup Reserve", "UEFA Champions League",
        ):
            with self.subTest(league=league):
                self.assert_not_aliased(dict(self.raw, league_raw=league))

    def test_efl_record_cannot_use_championship_fixture_or_mismatched_competition(self):
        for changes in (
            {"league_id": 18, "label": "ENG2", "league": "Championship"},
            {"league_id": 18}, {"league_id": 17}, {"label": "ENG2"},
            {"label": "ENG1"}, {"label": "eflc"}, {"label": ""}, {"label": None},
        ):
            with self.subTest(changes=changes):
                self.assert_not_aliased(fixtures=[dict(self.fx, **changes)])

    def test_championship_record_cannot_borrow_efl_competition_proof(self):
        record = dict(self.raw, league_raw="England Championship")
        self.assert_not_aliased(record)
        championship = dict(self.fx, league_id=18, label="ENG2", league="Championship")
        self.assertEqual(self.alias(record, [championship]),
                         ("everton", "wolverhampton", "England - Championship"))

    def test_all_fixture_identity_fields_require_positive_integer_ids(self):
        cases = {
            "sofa_id": (None, 0, -16992612, "16992612", True, False, 16992612.0),
            "league_id": (None, 0, -21, "21", True, False, 21.0),
            "away_id": (None, 0, -3, 48, "3", True, False, 3.0),
            "home_id": (None, 0, -48, 3, "48", True, False, 48.0),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_not_aliased(fixtures=[dict(self.fx, **{field: value})])

    def test_wolves_team_id_must_belong_to_the_correct_side(self):
        self.assert_not_aliased(fixtures=[dict(self.fx, home_id=3, away_id=48)])
        reversed_fixture = dict(self.fx, home="Wolverhampton", away="Everton",
                                _hn="wolverhampton", _an="everton", home_id=3, away_id=48)
        self.assertEqual(self.alias(fixtures=[reversed_fixture])[:2], ("everton", "wolverhampton"))
        self.assert_not_aliased(fixtures=[dict(reversed_fixture, home_id=48, away_id=3)])

    def test_wolves_home_and_reversed_raw_provider_order_preserve_orientation(self):
        record = dict(self.raw, home_raw="Wolves", away_raw="Everton")
        self.assertEqual(self.alias(record)[:2], ("wolverhampton", "everton"))
        self.assertFalse(self.report(record)["impure"])
        fixture = dict(self.fx, home="Wolverhampton", away="Everton",
                       _hn="wolverhampton", _an="everton", home_id=3, away_id=48)
        self.assertEqual(self.alias(record, [fixture])[:2], ("wolverhampton", "everton"))

    def test_raw_wolves_homonyms_and_category_suffixes_remain_distinct(self):
        for name in (
            "Wolves Women", "Wolves (Women)", "Wolves F", "Wolves U18", "Wolves U20",
            "Wolves U21", "Wolves B", "Wolves II", "Wolves Reservas", "Wolves Reserves",
            "Wolves Academy", "Wolves Youth", "Wollongong Wolves", "Wolves FC Women",
        ):
            with self.subTest(name=name):
                self.assert_not_aliased(dict(self.raw, away_raw=name))

    def test_opponent_must_match_exactly_without_fuzzy_or_category_relaxation(self):
        for name in (
            "Evert", "Evertoon", "Everton Women", "Everton U20", "Everton U21",
            "Everton B", "Everton II", "Everton Reserves", "Liverpool", "Wolves", "",
        ):
            with self.subTest(name=name):
                self.assert_not_aliased(dict(self.raw, home_raw=name))

    def test_fixture_names_must_match_both_teams_and_categories(self):
        for changes in (
            {"_hn": "liverpool"}, {"_hn": "everton women"}, {"_hn": "everton u21"},
            {"_hn": "everton b"}, {"_an": "wolves"}, {"_an": "wolverhampton women"},
            {"_an": "wolverhampton u20"}, {"_an": "wolverhampton b"}, {"_an": ""},
        ):
            with self.subTest(changes=changes):
                self.assert_not_aliased(fixtures=[dict(self.fx, **changes)])

    def test_future_exact_opponent_is_not_hardcoded_to_everton(self):
        fixture = dict(self.fx, home="Burnley", _hn="burnley", home_id=6)
        record = dict(self.raw, home_raw="Burnley")
        self.assertEqual(self.alias(record, [fixture])[:2], ("burnley", "wolverhampton"))
        self.assert_not_aliased(record)

    def test_missing_fixture_or_time_is_not_alias_evidence(self):
        self.assert_not_aliased(fixtures=[])
        for kickoff in (None, "", "invalid"):
            with self.subTest(kickoff=kickoff):
                self.assert_not_aliased(dict(self.raw, kickoff=kickoff))
        for stamp in (None, 0, "invalid"):
            with self.subTest(stamp=stamp):
                self.assert_not_aliased(fixtures=[dict(self.fx, start_ts=stamp)])

    def test_kickoff_45_minute_boundary_and_utc_equivalence(self):
        for minutes in (-45, 45):
            with self.subTest(minutes=minutes):
                record = dict(self.raw, kickoff=(self.dt + timedelta(minutes=minutes)).isoformat())
                self.assertEqual(self.alias(record)[:2], ("everton", "wolverhampton"))
        for seconds in (-2701, 2701):
            with self.subTest(seconds=seconds):
                record = dict(self.raw, kickoff=(self.dt + timedelta(seconds=seconds)).isoformat())
                self.assert_not_aliased(record)
        self.assertEqual(self.alias(dict(self.raw, kickoff="2026-09-16T18:45:00Z"))[:2],
                         ("everton", "wolverhampton"))

    def test_same_brt_day_is_mandatory_even_when_start_times_are_close(self):
        self.assert_not_aliased(day="2026-09-17")
        self.assert_not_aliased(fixtures=[dict(self.fx, day_brt="2026-09-17")])
        record = dict(self.raw, kickoff="2026-09-15T23:50:00-0300")
        fixture = dict(self.fx, start_ts=int(parse_start("2026-09-16T00:10:00-0300").timestamp()))
        self.assert_not_aliased(record, [fixture])

    def test_ambiguous_distinct_matching_events_are_rejected_even_with_expected_id(self):
        fixtures = [self.fx, dict(self.fx, sofa_id=123)]
        self.assert_not_aliased(fixtures=fixtures)
        self.assertTrue(self.report(fixtures=fixtures)["impure"])

    def test_nonmatching_candidates_do_not_block_unique_valid_evidence(self):
        fixtures = [dict(self.fx, sofa_id=123, _hn="liverpool"), self.fx]
        self.assertEqual(self.alias(fixtures=fixtures)[:2], ("everton", "wolverhampton"))

    def test_expected_sofa_id_binds_alias_to_existing_record(self):
        for sid in ("123", 123, 0, False, True, "", "016992612"):
            with self.subTest(expected_sofa_id=sid):
                self.assert_not_aliased(expected_sofa_id=sid)
        self.assertEqual(self.alias(expected_sofa_id=16992612)[:2], ("everton", "wolverhampton"))
        # No expected ID is the initial-matching API, still requiring unique fixture proof.
        self.assertEqual(self.alias(expected_sofa_id=None)[:2], ("everton", "wolverhampton"))
        self.assert_not_aliased(fixtures=[dict(self.fx, sofa_id=123)])

    def test_purity_supplies_expected_identity_to_every_proof(self):
        with patch("canonical.fixture_scoped_alias_pair", wraps=fixture_scoped_alias_pair) as guard:
            self.report()
        self.assertEqual(guard.call_count, 2)
        for call in guard.call_args_list:
            self.assertEqual(str(call.kwargs["expected_sofa_id"]), self.sid)

    def test_stored_normalization_confidence_and_claimed_context_are_not_proof(self):
        record = dict(self.raw, league_raw="USL Cup", alias_context="England - EFL Cup",
                      match_confidence=100, match_method="verified", home_norm="everton",
                      away_norm="wolverhampton", match_evidence={"strong": 100})
        self.assert_not_aliased(record)
        self.assertTrue(self.report(record)["impure"])
        self.assertTrue(self.report(fixtures=[])["impure"])

    def test_purity_cache_is_scoped_by_competition_and_kickoff(self):
        for invalid in (
            dict(self.raw, league_raw="England Championship"),
            dict(self.raw, league_raw="Scotland EFL Cup"),
            dict(self.raw, kickoff=(self.dt + timedelta(minutes=46)).isoformat()),
        ):
            with self.subTest(invalid=invalid):
                result = sofa_purity({"valid": self.raw, "anchor": self.anchor, "invalid": invalid},
                                     fixtures=[self.fx])[self.sid]
                self.assertTrue(result["impure"])
                self.assertEqual(result["n_keys"], 3)
                self.assertEqual(sorted(result["cluster_keys"]), [1, 2])

    def test_purity_cache_cannot_transfer_evidence_between_sofa_ids(self):
        keys = {"valid": self.raw, "anchor": self.anchor,
                "other_raw": dict(self.raw, sofa_id="123"),
                "other_anchor": dict(self.anchor, sofa_id="123")}
        result = sofa_purity(keys, fixtures=[self.fx])
        self.assertFalse(result[self.sid]["impure"])
        self.assertTrue(result["123"]["impure"])

    def test_default_purity_loads_current_fixture_evidence(self):
        with patch("canonical.load_sofa_fixtures", return_value=[self.fx]) as load:
            result = sofa_purity({"raw": self.raw, "anchor": self.anchor})
        load.assert_called_once_with()
        self.assertFalse(result[self.sid]["impure"])

    def test_alias_and_purity_do_not_mutate_records_or_fixtures(self):
        keys, fixtures = {"raw": self.raw, "anchor": self.anchor}, [self.fx]
        before = deepcopy((keys, fixtures))
        self.alias(fixtures=fixtures)
        sofa_purity(keys, fixtures=fixtures)
        self.assertEqual(sofa_purity(keys, only_ids={"other"}, fixtures=fixtures), {})
        self.assertEqual((keys, fixtures), before)


if __name__ == "__main__":
    unittest.main()
