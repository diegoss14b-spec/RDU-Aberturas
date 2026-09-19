"""Both reviewed English aliases need two exact team IDs and a unique fixture.

Regression: the 18/09 gate rejected 16391693 because West Brom was not the
fixture's exact West Bromwich Albion anchor, leaving Wolves unproved as well.
The EFL rows below are contract tests, not a claim that this fixture is a cup.
"""
from copy import deepcopy
from datetime import timedelta
from itertools import product
import unittest
from unittest.mock import patch

from canonical import fixture_scoped_alias_pair, norm_team, parse_start, sofa_purity


class TestIdentityPurityWolvesWestBrom(unittest.TestCase):
    def setUp(self):
        self.sid = "16391693"
        self.kickoff = "2026-09-20T11:00:00Z"
        self.dt = parse_start(self.kickoff)
        self.fx = {
            "sofa_id": 16391693, "home_id": 3, "away_id": 8,
            "home": "Wolverhampton", "away": "West Bromwich Albion",
            "_hn": "wolverhampton", "_an": "west bromwich albion",
            "league_id": 18, "label": "ENG2", "league": "Championship",
            "day_brt": "2026-09-20", "start_ts": int(self.dt.timestamp()),
        }
        self.raw = {
            "sofa_id": self.sid, "home_raw": "Wolves", "away_raw": "West Brom",
            "home_norm": "wolverhampton", "away_norm": "west bromwich albion",
            "league_raw": "Championship", "kickoff": self.kickoff,
        }
        self.anchor = dict(self.raw, home_raw="Wolverhampton", away_raw="West Bromwich Albion")

    def alias(self, record=None, fixtures=None, *, expected_sofa_id="16391693", day=None):
        record = self.raw if record is None else record
        start = parse_start(record.get("kickoff"))
        return fixture_scoped_alias_pair(
            norm_team(record["home_raw"]), norm_team(record["away_raw"]),
            record.get("league_raw", ""),
            day if day is not None else (start.strftime("%Y-%m-%d") if start else "?"),
            start, [self.fx] if fixtures is None else fixtures,
            expected_sofa_id=expected_sofa_id,
        )

    def assert_not_aliased(self, record=None, fixtures=None, **kwargs):
        record = self.raw if record is None else record
        self.assertEqual(
            self.alias(record, fixtures, **kwargs),
            (norm_team(record["home_raw"]), norm_team(record["away_raw"]),
             record.get("league_raw", "")),
        )

    def report(self, raw=None, fixtures=None):
        return sofa_purity(
            {"raw": self.raw if raw is None else raw, "anchor": self.anchor},
            fixtures=[self.fx] if fixtures is None else fixtures,
        )[self.sid]

    @staticmethod
    def reverse_fixture(fixture):
        out = dict(fixture)
        for h, a in (("home", "away"), ("_hn", "_an"), ("home_id", "away_id")):
            out[h], out[a] = fixture[a], fixture[h]
        return out

    def test_actual_championship_double_alias_purifies_without_dropping_keys(self):
        self.assertEqual(norm_team("Wolves"), "wolves")
        self.assertEqual(norm_team("West Brom"), "west brom")
        self.assertTrue(self.report(fixtures=[])["impure"])
        self.assertEqual(self.alias(),
                         ("wolverhampton", "west bromwich albion", "England - Championship"))
        result = self.report()
        self.assertFalse(result["impure"])
        self.assertEqual((result["n_clusters"], result["n_pairs"], result["n_keys"]), (1, 1, 2))

    def test_every_reviewed_name_pair_and_both_orientations_for_both_competitions(self):
        for competition, names, reverse_raw, reverse_fx in product(
            ((18, "ENG2", "Championship", "England - Championship"),
             (21, "EFLC", "EFL Cup", "England - EFL Cup")),
            product(("Wolves", "Wolverhampton"), ("West Brom", "West Bromwich Albion")),
            (False, True), (False, True),
        ):
            league_id, label, league, context = competition
            fixture = dict(self.fx, league_id=league_id, label=label, league=league)
            if reverse_fx:
                fixture = self.reverse_fixture(fixture)
            home, away = names[::-1] if reverse_raw else names
            record = dict(self.raw, home_raw=home, away_raw=away, league_raw=league)
            expected = ("west bromwich albion", "wolverhampton") if reverse_raw else \
                       ("wolverhampton", "west bromwich albion")
            with self.subTest(competition=league, names=names, raw_reverse=reverse_raw,
                              fixture_reverse=reverse_fx):
                self.assertEqual(self.alias(record, [fixture]), (*expected, context))

    def test_mixed_raw_abbreviations_collapse_to_one_pair_not_just_one_fuzzy_cluster(self):
        keys = {}
        for i, (home, away) in enumerate(product(
            ("Wolves", "Wolverhampton"), ("West Brom", "West Bromwich Albion"),
        )):
            keys[str(i)] = dict(self.raw, home_raw=home, away_raw=away)
        result = sofa_purity(keys, fixtures=[self.fx])[self.sid]
        self.assertEqual((result["n_keys"], result["n_pairs"], result["n_clusters"]), (4, 1, 1))
        self.assertEqual(result["clusters"], [["wolverhampton x west bromwich albion"]])

    def test_reviewed_league_spellings_remain_the_only_permitted_contexts(self):
        for league in ("Championship", "England Championship", "English Championship",
                       "Inglaterra Championship", "England - Championship",
                       "  INGLATERRA: CHAMPIONSHIP  "):
            with self.subTest(league=league):
                self.assertEqual(self.alias(dict(self.raw, league_raw=league))[:2],
                                 ("wolverhampton", "west bromwich albion"))
        fixture = dict(self.fx, league_id=21, label="EFLC", league="EFL Cup")
        for league in ("EFL Cup", "England EFL Cup", "English EFL Cup", "Inglaterra EFL Cup",
                       "England - EFL Cup"):
            with self.subTest(league=league):
                self.assertEqual(self.alias(dict(self.raw, league_raw=league), [fixture])[:2],
                                 ("wolverhampton", "west bromwich albion"))

    def test_unreviewed_leagues_and_categories_cannot_borrow_english_fixture(self):
        for league in ("", "England", "ENG2", "EFLC", "Premier League", "FA Cup", "Carabao Cup",
                       "League Cup", "UEFA Champions League", "Scotland Championship",
                       "USL Championship", "England Championship Women", "Championship U21",
                       "Championship Reserve", "EFL Cup Women", "Wales EFL Cup"):
            with self.subTest(league=league):
                self.assert_not_aliased(dict(self.raw, league_raw=league))

    def test_competition_ids_and_labels_cannot_be_crossed(self):
        for changes in ({"league_id": 21}, {"league_id": 17}, {"label": "EFLC"},
                        {"label": "eng2"}, {"label": ""}, {"label": None},
                        {"league_id": 21, "label": "EFLC", "league": "EFL Cup"}):
            with self.subTest(changes=changes):
                self.assert_not_aliased(fixtures=[dict(self.fx, **changes)])
        self.assert_not_aliased(dict(self.raw, league_raw="EFL Cup"))

    def test_all_fixture_ids_are_positive_typed_integers_even_in_championship(self):
        for field in ("sofa_id", "league_id", "home_id", "away_id"):
            for value in (None, 0, -1, True, False, str(self.fx[field]), float(self.fx[field])):
                for away in ("West Brom", "West Bromwich Albion"):
                    with self.subTest(field=field, value=value, away=away):
                        self.assert_not_aliased(dict(self.raw, away_raw=away),
                                                [dict(self.fx, **{field: value})])

    def test_wrong_ids_cannot_fall_through_to_old_single_alias_guard(self):
        # A fully spelled opponent formerly made the old Championship guard
        # accept ANY positive opponent ID. This known pair must fail closed.
        for changes in ({"home_id": 8, "away_id": 3}, {"home_id": 48},
                        {"away_id": 48}, {"home_id": 8}, {"away_id": 3}):
            for home, away in product(("Wolves", "Wolverhampton"),
                                      ("West Brom", "West Bromwich Albion")):
                with self.subTest(changes=changes, home=home, away=away):
                    self.assert_not_aliased(dict(self.raw, home_raw=home, away_raw=away),
                                            [dict(self.fx, **changes)])

    def test_fixture_names_and_ids_must_agree_in_both_orientations(self):
        for changes in ({"_hn": "west bromwich albion", "_an": "wolverhampton"},
                        {"_hn": "wolves"}, {"_an": "west brom"},
                        {"_hn": "everton"}, {"_an": "everton"},
                        {"_hn": "wolverhampton women"}, {"_an": "west bromwich albion u21"},
                        {"_hn": "wolverhampton b"}, {"_an": "west bromwich albion reserves"},
                        {"_hn": ""}, {"_an": ""}):
            with self.subTest(changes=changes):
                self.assert_not_aliased(fixtures=[dict(self.fx, **changes)])
        reverse = self.reverse_fixture(self.fx)
        self.assert_not_aliased(fixtures=[dict(reverse, home_id=3, away_id=8)])

    def test_raw_homonyms_unreviewed_abbreviations_and_category_suffixes_remain_distinct(self):
        for home in ("Wollongong Wolves", "Wolverhampt", "Wolves Women", "Wolves F", "Wolves U18",
                     "Wolves U20", "Wolves U21", "Wolves B", "Wolves II", "Wolves Reserves"):
            with self.subTest(home=home):
                self.assert_not_aliased(dict(self.raw, home_raw=home))
        for away in ("WBA", "West Bromwich", "West Brom Women", "West Brom F", "West Brom U21",
                     "West Brom B", "West Brom II", "West Brom Reserves", "West Bromwich Albon",
                     "Everton", "Wolves", ""):
            with self.subTest(away=away):
                self.assert_not_aliased(dict(self.raw, away_raw=away))

    def test_no_fixture_or_missing_time_never_counts_as_proof(self):
        self.assert_not_aliased(fixtures=[])
        for kickoff in (None, "", "not-a-time"):
            with self.subTest(kickoff=kickoff):
                self.assert_not_aliased(dict(self.raw, kickoff=kickoff))
        for timestamp in (None, 0, "invalid"):
            with self.subTest(timestamp=timestamp):
                self.assert_not_aliased(fixtures=[dict(self.fx, start_ts=timestamp)])

    def test_kickoff_45_minute_boundary_brt_and_utc_equivalence(self):
        for minutes in (-45, 45):
            record = dict(self.raw, kickoff=(self.dt + timedelta(minutes=minutes)).isoformat())
            with self.subTest(minutes=minutes):
                self.assertEqual(self.alias(record)[:2], ("wolverhampton", "west bromwich albion"))
        for seconds in (-2701, 2701):
            with self.subTest(seconds=seconds):
                self.assert_not_aliased(dict(self.raw, kickoff=(self.dt + timedelta(seconds=seconds)).isoformat()))
        self.assertEqual(self.alias(dict(self.raw, kickoff="2026-09-20T08:00:00-0300"))[:2],
                         ("wolverhampton", "west bromwich albion"))

    def test_same_brt_day_required_even_across_nearby_midnight(self):
        self.assert_not_aliased(day="2026-09-21")
        self.assert_not_aliased(fixtures=[dict(self.fx, day_brt="2026-09-21")])
        record = dict(self.raw, kickoff="2026-09-19T23:50:00-0300")
        fixture = dict(self.fx, start_ts=int(parse_start("2026-09-20T00:10:00-0300").timestamp()))
        self.assert_not_aliased(record, [fixture])

    def test_two_distinct_events_are_ambiguous_even_when_one_has_expected_id(self):
        fixtures = [self.fx, dict(self.fx, sofa_id=123)]
        for away in ("West Brom", "West Bromwich Albion"):
            with self.subTest(away=away):
                self.assert_not_aliased(dict(self.raw, away_raw=away), fixtures)
        self.assertTrue(self.report(fixtures=fixtures)["impure"])

    def test_duplicate_same_event_is_not_a_second_identity(self):
        self.assertEqual(self.alias(fixtures=[self.fx, dict(self.fx)])[:2],
                         ("wolverhampton", "west bromwich albion"))

    def test_nonmatching_extra_candidates_do_not_defeat_unique_valid_proof(self):
        fixtures = [dict(self.fx, sofa_id=123, _an="everton", away_id=48), self.fx]
        self.assertEqual(self.alias(fixtures=fixtures)[:2], ("wolverhampton", "west bromwich albion"))

    def test_expected_sofa_id_must_match_exactly_but_initial_matching_can_omit_it(self):
        for sid in ("123", 123, 0, True, False, "", "016391693", 16391693.0):
            with self.subTest(sid=sid):
                self.assert_not_aliased(expected_sofa_id=sid)
        for sid in (16391693, "16391693", None):
            with self.subTest(sid=sid):
                self.assertEqual(self.alias(expected_sofa_id=sid)[:2],
                                 ("wolverhampton", "west bromwich albion"))
        self.assert_not_aliased(fixtures=[dict(self.fx, sofa_id=123)])

    def test_real_wrong_opponent_stays_impure_not_laundered_by_stored_norms(self):
        invalid = dict(self.raw, away_raw="Everton", away_norm="west bromwich albion",
                       alias_context="England - Championship", match_confidence=100,
                       match_method="verified", match_evidence={"strong": 100})
        result = self.report(invalid)
        self.assertTrue(result["impure"])
        self.assertEqual(result["n_keys"], 2)
        self.assert_not_aliased(invalid)

    def test_stored_norms_and_claimed_context_do_not_substitute_for_fixture_or_league(self):
        record = dict(self.raw, league_raw="USL Championship", alias_context="England - Championship",
                      home_norm="wolverhampton", away_norm="west bromwich albion",
                      match_confidence=100, match_method="verified")
        self.assert_not_aliased(record)
        self.assertTrue(self.report(record)["impure"])
        self.assertTrue(self.report(fixtures=[])["impure"])

    def test_purity_cache_is_scoped_to_competition_and_kickoff_in_both_record_orders(self):
        for invalid in (dict(self.raw, league_raw="USL Championship"),
                        dict(self.raw, league_raw="EFL Cup"),
                        dict(self.raw, kickoff=(self.dt + timedelta(minutes=46)).isoformat())):
            for reverse_order in (False, True):
                items = [("valid", self.raw), ("anchor", self.anchor), ("invalid", invalid)]
                keys = dict(items[::-1] if reverse_order else items)
                with self.subTest(invalid=invalid, reverse_order=reverse_order):
                    result = sofa_purity(keys, fixtures=[self.fx])[self.sid]
                    self.assertTrue(result["impure"])
                    self.assertEqual(result["n_keys"], 3)
                    self.assertEqual(sorted(result["cluster_keys"]), [1, 2])

    def test_purity_cache_cannot_transfer_proof_to_another_sofa_id(self):
        keys = {"raw": self.raw, "anchor": self.anchor,
                "other_raw": dict(self.raw, sofa_id="123"),
                "other_anchor": dict(self.anchor, sofa_id="123")}
        result = sofa_purity(keys, fixtures=[self.fx])
        self.assertFalse(result[self.sid]["impure"])
        self.assertTrue(result["123"]["impure"])

    def test_purity_passes_expected_id_and_loads_current_fixture_if_omitted(self):
        with patch("canonical.load_sofa_fixtures", return_value=[self.fx]) as load, \
             patch("canonical.fixture_scoped_alias_pair", wraps=fixture_scoped_alias_pair) as guard:
            result = sofa_purity({"raw": self.raw, "anchor": self.anchor})
        load.assert_called_once_with()
        self.assertFalse(result[self.sid]["impure"])
        self.assertEqual(guard.call_count, 2)
        for call in guard.call_args_list:
            self.assertEqual(str(call.kwargs["expected_sofa_id"]), self.sid)

    def test_alias_and_purity_do_not_mutate_input_or_drop_filtered_identities(self):
        keys, fixtures = {"raw": self.raw, "anchor": self.anchor}, [self.fx]
        before = deepcopy((keys, fixtures))
        self.alias(fixtures=fixtures)
        first = sofa_purity(keys, fixtures=fixtures)
        second = sofa_purity(keys, fixtures=fixtures)
        self.assertEqual(first, second)
        self.assertEqual(sofa_purity(keys, only_ids={"other"}, fixtures=fixtures), {})
        self.assertEqual((keys, fixtures), before)


if __name__ == "__main__":
    unittest.main()
