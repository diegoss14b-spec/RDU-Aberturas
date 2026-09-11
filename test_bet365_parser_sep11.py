"""Offline regression suite: no network, main, or production log writes."""
import ast
import collections
import json
from pathlib import Path
import re
import unittest

import fetch_odds_bet365 as capture


def odds(header, name, price="1.833", **extra):
    return dict(header=header, name=name, odds=price, **extra)


def block(key, rows):
    return {"sp": {key: {"odds": rows}}}


class ParserRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.new = vars(capture)
        original = capture._detect_unknown_total
        cls.addClassCleanup(setattr, capture, '_detect_unknown_total', original)
        capture._detect_unknown_total = lambda *args: None

    def parse(self, payload):
        return self.new["parse_prematch"](payload, "H", "A")

    def test_alternative_corners_removed_even_if_upstream_omits_exactly(self):
        self.assertEqual(self.parse({"main": block("alternative_corners", [odds("Over", "7"), odds("Under", "7")])}), ({}, {}))

    def test_other_threeway_total_is_not_an_asian_push_pair(self):
        self.assertEqual(self.parse({"main": block("asian_total_corners", [odds("Over", "7"), odds("Exactly", "7"), odds("Under", "7")])}), ({}, {}))

    def test_no_cross_block_ou_pair(self):
        payload = {"main": block("number_of_cards_in_match", [odds("Over", "4.5")]),
                   "others": [block("asian_total_cards", [odds("Under", "4.5")])]}
        self.assertEqual(self.parse(payload), ({}, {}))

    def test_later_complete_block_survives_earlier_partial(self):
        payload = {"main": block("number_of_cards_in_match", [odds("Over", "4.5", "9")]),
                   "others": [block("asian_total_cards", [odds("Over", "4.5", "2.1"), odds("Under", "4.5", "1.8")])]}
        self.assertEqual(self.parse(payload)[0]["Cartões"], [{"linha": 4.5, "over": 2.1, "under": 1.8}])

    def test_first_complete_block_is_atomic(self):
        payload = {"main": block("number_of_cards_in_match", [odds("Over", "4.5", "2.1"), odds("Under", "4.5", "1.8")]),
                   "others": [block("asian_total_cards", [odds("Over", "4.5", "2.2"), odds("Under", "4.5", "1.7")])]}
        self.assertEqual(self.parse(payload)[0]["Cartões"], [{"linha": 4.5, "over": 2.1, "under": 1.8}])

    def test_conflicting_duplicate_invalidates_only_its_line(self):
        payload = {"main": block("number_of_cards_in_match", [odds("Over", "4.5", "2"), odds("Over", "4.5", "3"), odds("Under", "4.5"), odds("Over", "5.5"), odds("Under", "5.5")])}
        self.assertEqual([r["linha"] for r in self.parse(payload)[0]["Cartões"]], [5.5])

    def test_header_and_text_fallback_and_conflict(self):
        fn = self.new["_entry_side_line"]
        self.assertEqual(fn(odds("Over", "Over 5.5")), ("over", 5.5))
        self.assertEqual(fn(odds("", "Under 5.5")), ("under", 5.5))
        self.assertIsNone(fn(odds("Over", "Under 5.5")))
        self.assertIsNone(fn(odds("Over", "5.5", handicap="6.5")))

    def test_quarters_and_nonfinite_lines_are_rejected(self):
        fn = self.new["_entry_side_line"]
        for line in ("5.25", "5.75", "5,5.5", "nan", "Infinity", "-1"):
            self.assertIsNone(fn(odds("Over", line)), line)
        self.assertEqual(fn(odds("Over", "6")), ("over", 6.0))
        self.assertEqual(fn(odds("Over", 0)), ("over", 0.0))

    def test_decimal_prices_preserved_and_nonfinite_rejected(self):
        good = self.parse({"main": block("number_of_cards_in_match", [odds("Over", "4.5", "1.005"), odds("Under", "4.5", "1.833")])})
        self.assertEqual(good[0]["Cartões"][0]["over"], 1.005)
        self.assertEqual(good[0]["Cartões"][0]["under"], 1.833)
        for value in ("nan", "Infinity", "-Infinity", "1"):
            self.assertEqual(self.parse({"main": block("number_of_cards_in_match", [odds("Over", "4.5", value), odds("Under", "4.5")])}), ({}, {}))

    def test_handicap_perspective_zero_and_empty_fallback(self):
        for home, away, expected in (("-1", "+1", -1.0), (0, 0, 0.0)):
            parsed = self.parse({"main": block("asian_handicap_cards", [odds("1", "H", handicap=home), odds("2", "A", handicap=away)])})
            self.assertEqual(parsed[0]["Handicap de Cartões"][0]["linha"], expected)
        self.assertEqual(self.new["_hand_line"]("+0.5"), 0.5)
        self.assertIsNone(self.new["_hand_line"]("0.25"))

    def test_no_cross_block_handicap_pair(self):
        payload = {"main": block("asian_handicap_cards", [odds("1", "H", handicap="-1")]),
                   "others": [block("asian_handicap_cards", [odds("2", "A", handicap="+1")])]}
        self.assertEqual(self.parse(payload), ({}, {}))

    def test_no_cross_block_team_pair_or_wrong_side(self):
        payload = {"main": block("team_cards", [odds("1", "Over 2.5")]),
                   "others": [block("team_cards", [odds("1", "Under 2.5"), odds("2", "Over 2.5")])]}
        self.assertEqual(self.parse(payload), ({}, {}))

    def test_malformed_optional_sections_and_rows_do_not_crash(self):
        self.assertEqual(self.parse({"main": [], "others": [None, [], {"sp": []}, {"sp": {"team_cards": None}}]}), ({}, {}))
        self.assertEqual(self.parse({"main": block("number_of_cards_in_match", [None, "bad"])}), ({}, {}))


if __name__ == '__main__': unittest.main()
