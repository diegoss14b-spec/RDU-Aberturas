from datetime import datetime, timedelta
from capture_discovery import DiscoveryQueue
from history_quality import BRT

NOW = datetime(2026,9,8,12,tzinfo=BRT)

def test_rotation_crosses_old_fixed_cap_without_more_requests(tmp_path):
    events = [{"id":i} for i in range(176)]
    path = tmp_path / "betano_discovery.json"
    seen = set()
    for cycle in range(5):
        queue = DiscoveryQueue(path,NOW+timedelta(minutes=20*cycle))
        selected = queue.select(events,60)
        assert len(selected) <= 60
        for event in selected:
            seen.add(event["id"])
            queue.record(event["id"],success=True,useful=event["id"] < 10)
        queue.save()
    assert seen == set(range(176))
    assert queue.metrics["inventory"] == 176
    assert queue.metrics["not_selected"] == 116

def test_refresh_useful_preserves_exploration_budget(tmp_path):
    path = tmp_path / "queue.json"
    q = DiscoveryQueue(path,NOW)
    events = [{"id":i} for i in range(100)]
    for i in range(80):
        q.record(i,success=True,useful=True)
    selected = q.select(events,20)
    assert len(selected) == 20
    assert sum(e["id"] >= 80 for e in selected) >= 5

def test_failures_remain_retryable_and_deduped(tmp_path):
    q = DiscoveryQueue(tmp_path / "queue.json",NOW)
    events = [{"id":1},{"id":1},{"id":2}]
    assert len(q.select(events,120)) == 2
    q.record(1,success=False); q.record(2,success=True,useful=False)
    q.save()
    again = DiscoveryQueue(q.path,NOW+timedelta(minutes=20))
    assert len(again.select(events,120)) == 2
    assert q.metrics["errors"] == 1
    assert q.metrics["succeeded"] == 1

def test_zero_budget_and_damaged_state_do_not_capture(tmp_path):
    path = tmp_path / "queue.json"; path.write_text("broken")
    q = DiscoveryQueue(path,NOW)
    assert q.select([{"id":1}],0) == []
    q.save()
