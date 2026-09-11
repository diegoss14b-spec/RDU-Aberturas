"""Portable policy regressions and real collector wiring, without HTTP."""
import ast
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from capture_discovery import ACTIVE_FT_FAMILIES, DiscoveryQueue


NOW = datetime(2026, 9, 11, 19, 31, tzinfo=timezone.utc)


def queue(tmp_path, rows):
    q = DiscoveryQueue(tmp_path / "not-persisted.json", NOW)
    q.rows = copy.deepcopy(rows)
    return q


def known(markets, age_hours=1):
    return {"useful": True, "markets": list(markets),
            "last_attempt": (NOW - timedelta(hours=age_hours)).isoformat()}


def disabled_canon(disabled):
    # Restrict this proposal to the confirmed disabled-corners defect. Do not
    # broaden the meaning of other existing Betano tab/config aliases here.
    return {"Escanteios"} if "escanteios" in disabled else set()


def test_disabled_corners_stop_consuming_known_reserve_but_remain_explorable(tmp_path):
    rows = {f"corner-{i}": known(["Escanteios"], 5) for i in range(90)}
    rows.update({f"active-{i}": known(["Cartões"], 1) for i in range(45)})
    events = [{"id": ident} for ident in rows] + [{"id": f"new-{i}"} for i in range(15)]
    q = queue(tmp_path, rows)
    before = copy.deepcopy(q.rows)
    selected = q.select(events, 60, ignored_markets=disabled_canon({"escanteios"}))
    assert len(selected) == len({e["id"] for e in selected}) == 60
    assert [e["id"] for e in selected[:45]] == [f"active-{i}" for i in range(45)]
    assert [e["id"] for e in selected[45:]] == [f"new-{i}" for i in range(15)]
    assert q.metrics["exploration_min_budget"] == 15
    assert q.rows == before
    corners = [{"id": f"corner-{i}"} for i in range(90)]
    assert len(q.select(corners, 60, ignored_markets={"Escanteios"})) == 60


def test_record_marks_only_disabled_family_as_not_useful_without_erasing_inventory(tmp_path):
    q = queue(tmp_path, {})
    recognized = {"Escanteios"}
    ignored = disabled_canon({"escanteios"})
    q.record("corner", success=True, useful=bool(recognized - ignored), markets=recognized)
    assert q.rows["corner"]["useful"] is False
    assert q.rows["corner"]["markets"] == ["Escanteios"]
    assert q.metrics["succeeded"] == 1
    assert q.metrics["useful"] == 0


def test_mixed_active_and_disabled_markets_remain_useful(tmp_path):
    q = queue(tmp_path, {})
    recognized = {"Escanteios", "Desarmes"}
    ignored = disabled_canon({"escanteios"})
    q.record("mixed", success=True, useful=bool(recognized - ignored), markets=recognized)
    assert q.rows["mixed"]["useful"] is True
    assert set(q.rows["mixed"]["markets"]) == recognized
    assert q.metrics["useful"] == 1


def test_no_disabled_markets_keeps_original_priority_and_usefulness(tmp_path):
    rows = {"corner": known(["Escanteios"], 5), "cards": known(["Cartões"], 1)}
    events = [{"id": k} for k in rows]
    q1, q2 = queue(tmp_path, rows), queue(tmp_path, rows)
    assert q1.select(events, 1) == q2.select(events, 1, ignored_markets=disabled_canon(set()))
    assert bool({"Escanteios"} - disabled_canon(set())) is True


def test_optional_family_coverage_protects_rare_family_without_extra_slots(tmp_path):
    rows = {f"cards-{i}": known(["Cartões"], 5) for i in range(100)}
    rows.update({f"rare-{i}": known(["Desarmes", "Laterais", "Tiros de meta"], 1)
                 for i in range(20)})
    events = [{"id": ident} for ident in rows] + [{"id": f"new-{i}"} for i in range(15)]
    q1, q2 = queue(tmp_path, rows), queue(tmp_path, rows)
    old = q1.select(events, 60, ignored_markets={"Escanteios"})
    balanced = q2.select(events, 60, ignored_markets={"Escanteios"},
                         coverage_markets=ACTIVE_FT_FAMILIES)
    assert not any(e["id"].startswith("rare-") for e in old)
    assert sum(e["id"].startswith("rare-") for e in balanced) == 20
    assert len(balanced) == len({e["id"] for e in balanced}) == 60
    assert [e["id"] for e in old[-15:]] == [e["id"] for e in balanced[-15:]]
    assert q2.metrics["exploration_min_budget"] == 15


@pytest.mark.parametrize("budget", [0, 1, 2, 7, 15, 60])
def test_both_options_keep_requested_budget_and_nonmutating_state(tmp_path, budget):
    rows = {str(i): known(["Escanteios"] if i % 2 else ["Cartões", "Faltas"])
            for i in range(75)}
    events = [{"id": ident} for ident in rows]
    for families in ((), ACTIVE_FT_FAMILIES):
        q = queue(tmp_path, rows)
        before = copy.deepcopy(q.rows)
        selected = q.select(events, budget, ignored_markets={"Escanteios"}, coverage_markets=families)
        assert len(selected) == len({e["id"] for e in selected}) == budget
        assert q.rows == before


def test_real_collector_passes_active_policy_and_records_real_normalized_utility(monkeypatch, tmp_path):
    import capture_common
    import capture_discovery
    instances = []
    class SpyQueue(DiscoveryQueue):
        def __init__(self, *args):
            super().__init__(*args)
            instances.append(self)
        def select(self, events, budget, **kwargs):
            self.selected_policy = kwargs
            return super().select(events, budget, **kwargs)
        def save(self):
            pass
    monkeypatch.setattr(capture_discovery, 'DiscoveryQueue', SpyQueue)
    monkeypatch.setattr(capture_common, 'write_odds_latest', lambda *a, **k: {})
    def get(url):
        if url.endswith('jogos-de-hoje/'):
            return {'data': {'blocks': [{'events': [
                {'id': str(i), 'name': 'Alpha - Bravo', 'url': f'/event/{i}',
                 'leagueName': 'Test', 'startTime': '2026-09-12T20:00:00Z'} for i in range(3)
            ]}]}}
        if url.endswith('/2'):
            return None
        name = 'Escanteios' if url.endswith('/0') else 'Total de Cartões'
        return {'data': {'event': {'markets': [{'name': name, 'selections': [
            {'name': 'Mais', 'handicap': 4.5, 'price': 1.9},
            {'name': 'Menos', 'handicap': 4.5, 'price': 1.9}
        ]}]}}}
    source = Path(__file__).with_name('fetch_odds_betano.py').read_text()
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('main', 'extract_ou', 'extract_1x2')]
    module = ast.Module(body=functions, type_ignores=[])
    namespace = {'get': get, 'log': lambda *a: None, 'BASE': 'https://fixture.invalid',
                 'OUT': tmp_path, 'BRT': timezone(timedelta(hours=-3)), 'datetime': datetime,
                 'MAX_EVENTS': 60, 'MIN_EVENTS': 15, 'MIN_EFF': 15, '_MK_OFF': {'escanteios'},
                 'TABS': {}, 'time': SimpleNamespace(sleep=lambda *a: None), 'json': json,
                 're': re, 'odds_window': lambda: None}
    exec(compile(module, 'fetch_odds_betano-isolated-main', 'exec'), namespace)
    assert namespace['main']() == 2
    q = instances[0]
    assert q.selected_policy == {'ignored_markets': {'Escanteios'}, 'coverage_markets': ACTIVE_FT_FAMILIES}
    assert q.metrics['attempted'] == 3 and q.metrics['useful'] == 1
    assert not q.rows['0']['useful'] and q.rows['0']['markets'] == ['Escanteios']
    assert q.rows['1']['useful'] and q.rows['1']['markets'] == ['Cartões']
    assert not q.rows['2'].get('useful')
