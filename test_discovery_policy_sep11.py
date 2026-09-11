from datetime import datetime, timezone

from capture_discovery import DiscoveryQueue
from bet365_capture_plan import league_priority, eligible_events, merge_inventory


def test_ignored_only_markets_do_not_consume_known_useful_budget(tmp_path):
    q = DiscoveryQueue(tmp_path / 'q.json', datetime.now(timezone.utc))
    q.rows = {'corner': {'useful': True, 'markets': ['Escanteios']},
              'cards': {'useful': True, 'markets': ['Cartões']}}
    selected = q.select([{'id':'corner'}, {'id':'cards'}], 2, ignored_markets={'Escanteios'})
    assert selected[0]['id'] == 'cards'
    assert len(selected) == 2  # corner-only still eligible for exploration


def test_priority_does_not_starve_older_exploration(tmp_path):
    q = DiscoveryQueue(tmp_path / 'q.json', datetime.now(timezone.utc))
    q.rows = {'new': {'last_attempt':'2026-09-10T12:00:00Z'},
              'old': {'last_attempt':'2026-09-09T12:00:00Z'}}
    events = [{'id':'new','priority':0}, {'id':'old','priority':2}]
    assert q.select(events, 1, priority_key=lambda e:e['priority'])[0]['id'] == 'old'


def test_7k_disabled_corners_do_not_displace_five_future_rich_games(tmp_path):
    q = DiscoveryQueue(tmp_path / 'q.json', datetime.now(timezone.utc))
    corners = [{'id':str(i)} for i in range(150)]
    rich = [{'id':'rich'+str(i)} for i in range(5)]
    for event in corners:
        q.rows[event['id']] = {'useful':True, 'markets':['Escanteios'], 'last_attempt':'2026-09-10T12:00:00Z'}
    for event in rich:
        q.rows[event['id']] = {'useful':True, 'markets':['Desarmes','Laterais','Tiros de meta'], 'last_attempt':'2026-09-11T14:41:00Z'}
    selected = q.select(corners+rich, 120, ignored_markets={'Escanteios'})
    assert {e['id'] for e in rich} <= {e['id'] for e in selected}
    assert len(selected) == 120 and q.metrics['exploration_min_budget'] == 30


def test_exact_league_does_not_match_youth_or_minor_substrings():
    assert league_priority({'league':'England Premier League'}) == 0
    assert league_priority({'league':'England Premier League U21'}) == 2
    assert league_priority({'league':'Russia Premier League'}) == 2


def test_inventory_updated_to_past_is_evicted_and_future_retained():
    now = 2000000000
    a = {'fi':'a','time':now+3600,'home':'H','away':'A','league':'England Premier League'}
    b = dict(a, fi='b')
    assert merge_inventory([a,b], [dict(a,time=now-1)], now) == [b]
    assert eligible_events([a,dict(a,league='Virtual SRL')],now) == [a]
    assert eligible_events([a],now,hours=2,min_lead=3600) == []
