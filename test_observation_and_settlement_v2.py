import json
from datetime import datetime, timedelta
from unittest.mock import patch
import pytest
import history_ingest as ing
import history_close as close
import history_settle as settle
from history_shard import load_month
from history_quality import BRT, strict_clv_reason
from history_merge import merge_records, merge_latest_state
from observation_clock import observation_meta, observation_reason, close_age_band
from cards_settlement import card_decision
from settlement_revisions import latest_revisions
from review_legacy_settlements import plan
from test_history_settlement import record, result_row
from test_history_metrics import settled

class Clock(datetime):
    current = datetime(2026, 9, 8, 9, 0, tzinfo=BRT)
    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

@pytest.fixture
def replay(tmp_path, monkeypatch):
    odds, hist = tmp_path / "odds", tmp_path / "history"
    odds.mkdir(); hist.mkdir()
    event = {"captured_at": "2026-09-08T08:00:00-03:00", "event_id": "test999",
             "name": "Audit Alpha - Audit Beta", "start": "2026-09-08T12:00:00-03:00",
             "league": "Audit League", "mercados": {"Cartões": [{"linha": 4.5, "over": 1.9, "under": 1.9}]}}
    (odds / "7k_latest.json").write_text(json.dumps({"file": "fixed.jsonl"}))
    identity = {"day": "2026-09-08", "hn": "audit alpha", "an": "audit beta",
                "sofa_id": 99999999, "kickoff_iso": event["start"], "match_method": "pair"}
    for module in (ing, close):
        monkeypatch.setattr(module, "HIST", hist)
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(ing, "ODDS", odds)
    monkeypatch.setattr(ing, "HOUSE_MAP", hist / "map.json")
    monkeypatch.setattr(ing, "CASAS", ["7k"])
    monkeypatch.setattr(ing, "load_sofa_fixtures", lambda: [])
    monkeypatch.setattr(ing, "resolve_identity", lambda *a: identity)
    def run(hour, minute=0, captured=None):
        if captured is not None:
            event["captured_at"] = captured
        (odds / "fixed.jsonl").write_text(json.dumps(event) + "\n")
        Clock.current = datetime(2026, 9, 8, hour, minute, tzinfo=BRT)
        ing.main()
        return get()
    def get():
        keys = load_month(hist / "keys", "2026-09")
        return next(v for k, v in keys.items() if not k.startswith("__"))
    return run, get, hist

def test_same_snapshot_does_not_become_a_close(replay):
    run, get, hist = replay
    first = run(9)
    second = run(11, 58)
    assert first["last_ts"] == second["last_ts"] == "2026-09-08T08:00:00-03:00"
    assert second["n_obs"] == 1
    Clock.current = Clock.current.replace(minute=59)
    close.main()
    rec = get()
    assert rec["close_ts"] == first["last_ts"]
    assert close_age_band(rec) == "older_than_60m"
    assert strict_clv_reason({**rec, "status": "settled"}) == "close_older_than_60m"
    ticks = [json.loads(x) for x in (hist / "ticks" / "2026-09-08.jsonl").read_text().splitlines()]
    assert all(t["ts"] == first["last_ts"] for t in ticks)

def test_new_same_price_is_real_observation_and_postko_does_not_corrupt_close(replay):
    run, get, _ = replay
    run(9)
    pre = run(11, 58, "2026-09-08T11:57:00-03:00")
    assert pre["n_obs"] == 2 and pre["n_moves"] == 0
    post = run(12, 1, "2026-09-08T12:01:00-03:00")
    assert post["last_ts"] == pre["last_ts"]
    assert post["last_observed_at"] == pre["last_observed_at"]
    close.main()
    assert strict_clv_reason({**get(), "status": "settled"}) is None

@pytest.mark.parametrize("stamp,reason", [(None,"missing_observed_at"),
    ("invalid", "missing_observed_at"), ("2026-09-08T15:00:00-03:00", "future_observed_at")])
def test_missing_future_clock_fails_closed(stamp, reason):
    ev = observation_meta({"captured_at": stamp}, "raw")
    assert observation_reason(ev, datetime(2026,9,8,12,tzinfo=BRT)) == reason

def test_out_of_order_does_not_replace_price(replay):
    run, get, _ = replay
    run(11, 30, "2026-09-08T11:20:00-03:00")
    run(11, 40, "2026-09-08T09:00:00-03:00")
    assert get()["last_ts"] == "2026-09-08T11:20:00-03:00"
    assert get()["n_obs"] == 1

@pytest.mark.parametrize("age,band,valid", [(15,"within_15m",True),(30,"within_30m",True),
    (60,"within_60m",True),(61,"older_than_60m",False)])
def test_close_age_policy(age, band, valid):
    rec = settled()
    ts = (datetime.fromisoformat(rec["kickoff"]) - timedelta(minutes=age)).isoformat()
    rec.update(close_ts=ts, close_observed_at=ts)
    assert close_age_band(rec) == band
    assert (strict_clv_reason(rec) is None) is valid

def test_legacy_is_preserved_but_not_strict():
    rec = settled(); rec.pop("open_time_verified")
    before = dict(rec)
    assert strict_clv_reason(rec) == "observation_time_unknown"
    assert rec == before

def test_merge_never_borrows_evidence():
    modern = settled(); modern.update(last_ts=modern["close_ts"], last_time_verified=True)
    legacy = {"open_ts": "2025-01-01", "open_odd": 2, "close_ts": "2027-01-01",
              "close_odd": 2, "last_ts": "2027-01-01", "last_odd": 2}
    merged = merge_records(modern, legacy)
    assert merged.get("open_time_verified") is not True
    assert merged.get("close_time_verified") is not True
    assert merged.get("last_time_verified") is not True
    assert merge_latest_state({"ts":"2026", "observed_at":"2026"}, {"ts":"2027"}) == {"ts":"2027"}

@pytest.mark.parametrize("row,line,result,won,rule", [
    ({"cards":5,"cards_r2":6,"red_cards":1},5.5,6,True,"red2_incidents"),
    ({"cards":5,"cards_r2":5,"red_cards":1},5.5,5,False,"red2_incidents"),
    ({"cards":5,"red_cards":0},5,5,None,"red2_no_reds"),
    ({"cards":5,"red_cards":1},4.5,None,True,"red2_outcome_invariant"),
    ({"cards":5,"red_cards":1},5.5,None,None,"red2_pending_incidents"),
    ({"cards":5},4.5,None,None,"red2_pending_incidents")])
def test_card_semantics(row, line, result, won, rule):
    d = card_decision(row, line, "over")
    assert (d["result"], d["won"], d["rule"]) == (result, won, rule)

def test_pending_then_revised_and_append_idempotent(tmp_path, monkeypatch):
    key = "betano|2026-07-17|time alpha|time beta|Cartões|4.5|over"
    rec = record(); now = datetime(2026,7,18,12,tzinfo=BRT)
    row = result_row(cards=5,yellow_cards=4,red_cards=1)
    _,_,r1 = settle.settle_one(key, rec, [row], now)
    original = dict(rec)
    assert settle.settle_one(key,rec,[row],now)[0] == "unchanged"
    assert rec == original
    _,_,r2 = settle.settle_one(key,rec,[{**row,"cards_r2":6}],now+timedelta(hours=1))
    assert rec["result"] == 6
    assert r2["supersedes_settlement_revision"] == r1["settlement_revision"]
    monkeypatch.setattr(settle,"HIST",tmp_path)
    settle._append_clv([r1,r1,r2]); settle._append_clv([r1,r2])
    rows = [json.loads(x) for p in (tmp_path / "clv").glob("*.jsonl") for x in p.read_text().splitlines()]
    assert len(rows) == 2
    assert len(latest_revisions(rows)) == 1
    assert latest_revisions(rows)[0]["result"] == 6

def test_legacy_plan_is_read_only(tmp_path):
    path = tmp_path / "old.json"
    key = "betano|sofa:1|Cartões|4.5|over"
    raw = json.dumps({key:{"status":"settled","result":5,"settlement_rule":"r1_fallback"}})
    path.write_text(raw)
    result = plan([path])
    assert result["dry_run"] and result["mutations"] == 0
    assert result["counts"]["card_semantics_review_required"] == 1
    assert path.read_text() == raw

def test_ledger_emits_revisions_once_not_duplicate_bets(tmp_path):
    from build_model_ledger import emit_ledger
    from test_model_ledger import rec, fixidx, NOW
    key = "betano|sofa:555|Cartões|4.5|over"
    row = rec(status="settled",m_ts=NOW.isoformat(),settlement_revision="a",result=None,won=True)
    emit_ledger({key:row},fixidx(),{}, {},NOW,ledger_dir=tmp_path)
    row.pop("m_emitted"); row.update(settlement_revision="b",supersedes_settlement_revision="a",result=6)
    emit_ledger({key:row},fixidx(),{}, {},NOW,ledger_dir=tmp_path)
    row.pop("m_emitted")
    emit_ledger({key:row},fixidx(),{}, {},NOW,ledger_dir=tmp_path)
    rows = [json.loads(x) for p in tmp_path.glob("*.jsonl") for x in p.read_text().splitlines()]
    assert len(rows) == 2
    assert len(latest_revisions(rows)) == 1
    assert rows[-1]["supersedes_settlement_revision"] == "a"

def test_inconsistent_card_evidence_never_settles():
    d = card_decision({"cards":5,"red_cards":1,"cards_r2":12},5.5,"over")
    assert d["pending"] and d["result"] is None

def test_openclose_keeps_legacy_but_labels_unknown():
    from build_openclose import build_row
    from copy import deepcopy
    rec = settled(result=5)
    rec.update(settlement_rule="red2_incidents")
    group = {"kickoff":rec["kickoff"],"jogo":"Alpha - Beta","result":5,
             "casas":{"betano":{4.5:{"over":dict(rec),"under":dict(rec)}}}}
    modern = build_row("sofa:1","Cartões",group)
    assert modern["observation_time_verified"] and modern["close_within_60m"]
    assert modern["settlement_verified"] and modern["lado"] == "over"
    old = deepcopy(group)
    for value in old["casas"]["betano"][4.5].values():
        value.pop("open_time_verified"); value.pop("settlement_rule")
    legacy = build_row("sofa:1","Cartões",old)
    assert legacy["resultado"] == 5 and legacy["mu_close"] is not None
    assert legacy["observation_time_verified"] is False and legacy["settlement_verified"] is False
    assert legacy["lado"] is None
