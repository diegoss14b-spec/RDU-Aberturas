"""Portable CI tests against the real queue. No fixture, network or writes."""
import copy
from datetime import datetime
import random
import unittest

from capture_discovery import ACTIVE_FT_FAMILIES, DiscoveryQueue


def queue(rows):
    q = DiscoveryQueue.__new__(DiscoveryQueue)
    q.rows = copy.deepcopy(rows)
    q.metrics = {}
    return q


def record(markets, timestamp="2026-09-11T10:00:00Z", useful=True):
    return {"markets": markets, "last_attempt": timestamp, "useful": useful}


def legacy_reference(events, rows, budget, priority=None, ignored=()):
    """Independent compact oracle for the pre-coverage policy, frozen here.

    This never calls DiscoveryQueue.select or the proposed implementation.
    Test timestamps are valid ISO or missing, avoiding parser coupling.
    """
    unique = {}
    for event in events:
        ident = event.get("id")
        if ident is not None and ident != "":
            unique.setdefault(str(ident), event)
    order = {ident: i for i, ident in enumerate(unique)}

    def clock(ident):
        raw = rows.get(ident, {}).get("last_attempt")
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() if raw else float("-inf")

    def tier(ident):
        return priority(unique[ident]) if priority else 0

    cap = max(0, int(budget))
    explore = max(1, (cap+3)//4) if cap else 0
    known = [ident for ident in unique if rows.get(ident, {}).get("useful")
             and (not ignored or set(rows[ident].get("markets") or [])-set(ignored))]
    known.sort(key=lambda ident: (tier(ident), clock(ident), order[ident]))
    chosen = known[:max(0, cap-explore)]
    remainder = [ident for ident in unique if ident not in set(chosen)]
    remainder.sort(key=lambda ident: (clock(ident), tier(ident), order[ident]))
    return [unique[ident] for ident in chosen+remainder[:cap-len(chosen)]]


class DiscoveryCoverageTests(unittest.TestCase):
    def test_active_family_contract_is_exact_and_excludes_corners(self):
        self.assertEqual(ACTIVE_FT_FAMILIES, (
            "Cartões", "Chutes no gol", "Desarmes", "Faltas", "Finalizações",
            "Impedimentos", "Laterais", "Tiros de meta",
        ))

    def test_default_matches_independent_legacy_reference(self):
        rng = random.Random(110926)
        for _ in range(80):
            events = [{"id": str(i), "priority": i % 3} for i in range(90)]
            rng.shuffle(events)
            events += [events[0], {"id": None}, {}]
            rows = {str(i): record(rng.sample(list(ACTIVE_FT_FAMILIES)+["Escanteios"], rng.randrange(4)),
                                  f"2026-09-{rng.randrange(1,12):02}T10:00:00Z", i % 4 != 0)
                    for i in range(70)}
            budget = rng.randrange(0, 110)
            priority = lambda e: e["priority"]
            expected = legacy_reference(events, rows, budget, priority, {"Escanteios"})
            actual = queue(rows).select(events, budget, priority_key=priority, ignored_markets={"Escanteios"})
            self.assertEqual(actual, expected)

    def test_five_rich_ids_are_not_starved_by_older_cards_only(self):
        targets = ["882302690376577024", "881981248845918208", "882302690980630528",
                   "882302690691149824", "882302691647451136"]
        cards = [{"id": f"cards{i}"} for i in range(160)]
        rich = [{"id": ident} for ident in targets]
        unseen = [{"id": f"unseen{i}"} for i in range(40)]
        events = cards+rich+unseen
        rows = {e["id"]: record(["Cartões"]) for e in cards}
        rows.update({e["id"]: record(list(ACTIVE_FT_FAMILIES), "2026-09-11T14:00:00Z") for e in rich})
        self.assertEqual(queue(rows).select(events, 120, ignored_markets={"Escanteios"}), cards[:90]+unseen[:30])
        q = queue(rows)
        original_events = copy.deepcopy(events)
        selected = q.select(events, 120, ignored_markets={"Escanteios"}, coverage_markets=ACTIVE_FT_FAMILIES)
        self.assertEqual(selected[:6], cards[:1]+rich)
        self.assertTrue(all(e in selected[:90] for e in rich))
        self.assertEqual(selected[90:], unseen[:30])
        self.assertEqual(len(selected), 120)
        self.assertEqual(len({e["id"] for e in selected}), 120)
        self.assertEqual(q.metrics["exploration_min_budget"], 30)
        self.assertEqual(q.metrics["attempted"], 0)
        self.assertEqual(q.rows, rows)
        self.assertEqual(events, original_events)

    def test_round_robin_balances_disjoint_families_in_known_reserve(self):
        families = ("Cartões", "Chutes no gol", "Desarmes")
        events = [{"id": f"{prefix}{i}"} for prefix in "abc" for i in range(5)]
        rows = {f"{prefix}{i}": record([family]) for prefix, family in zip("abc", families) for i in range(5)}
        events += [{"id": "u0"}, {"id": "u1"}]
        selected = queue(rows).select(events, 8, coverage_markets=families)
        self.assertEqual([e["id"] for e in selected], ["a0", "b0", "c0", "a1", "b1", "c1", "u0", "u1"])

    def test_per_family_age_priority_and_input_ties_are_preserved(self):
        events = [{"id": "new", "p": 0}, {"id": "old", "p": 0}, {"id": "low", "p": 1}]
        rows = {"new": record(["Desarmes"], "2026-09-11T12:00:00Z"),
                "old": record(["Desarmes"], "2026-09-10T12:00:00Z"),
                "low": record(["Desarmes"], "2026-09-09T12:00:00Z")}
        selected = queue(rows).select(events, 3, coverage_markets=["Desarmes"], priority_key=lambda e: e["p"])
        self.assertEqual([e["id"] for e in selected], ["old", "new", "low"])
        tied = {e["id"]: record(["Desarmes"]) for e in events}
        selected = queue(tied).select(events, 3, coverage_markets=["Desarmes"])
        self.assertEqual(selected, events)

    def test_exploration_uses_age_before_league_priority(self):
        events = [{"id": "higher", "p": 0}, {"id": "older", "p": 2}]
        rows = {"higher": record([], "2026-09-11T10:00:00Z", False),
                "older": record([], "2026-09-10T10:00:00Z", False)}
        selected = queue(rows).select(events, 1, priority_key=lambda e: e["p"], coverage_markets=ACTIVE_FT_FAMILIES)
        self.assertEqual([e["id"] for e in selected], ["older"])

    def test_ignored_family_and_duplicate_family_do_not_consume_known_twice(self):
        events = [{"id": "corner"}, {"id": "cards"}]
        rows = {"corner": record(["Escanteios"]), "cards": record(["Cartões"])}
        selected = queue(rows).select(events, 2, ignored_markets={"Escanteios"},
                                      coverage_markets=["Escanteios", "Cartões", "Cartões"])
        self.assertEqual([e["id"] for e in selected], ["cards", "corner"])

    def test_exhausted_families_fall_back_to_other_known_by_age(self):
        events = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "u"}]
        rows = {"a": record(["Other"], "2026-09-11T10:00:00Z"),
                "b": record(["Other"], "2026-09-10T10:00:00Z"), "c": record(["Desarmes"])}
        selected = queue(rows).select(events, 4, coverage_markets=["Desarmes"])
        self.assertEqual([e["id"] for e in selected], ["c", "b", "a", "u"])

    def test_zero_small_budget_duplicate_identity_and_custom_field(self):
        events = [{"_id": 1}, {"_id": "1"}, {"_id": "2"}, {"_id": None}]
        rows = {"1": record(list(ACTIVE_FT_FAMILIES))}
        for budget in [-1, 0, 1, 2, 3, 20]:
            selected = queue(rows).select(events, budget, id_field="_id", coverage_markets=ACTIVE_FT_FAMILIES)
            self.assertEqual(len(selected), min(2, max(0, budget)))
            self.assertEqual(len({str(e["_id"]) for e in selected}), len(selected))

    def test_state_only_identity_is_never_resurrected(self):
        rows = {"absent": record(list(ACTIVE_FT_FAMILIES)), "present": record(["Cartões"])}
        selected = queue(rows).select([{"id": "present"}], 120, coverage_markets=ACTIVE_FT_FAMILIES)
        self.assertEqual(selected, [{"id": "present"}])


if __name__ == "__main__":
    unittest.main()

