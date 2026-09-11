"""Bounded discovery/revisit queue; no network and no odds fabrication.

State lives under _status so the existing snapshot persistence/reconciliation
owns it. At least 25% of the request budget explores oldest/unseen candidates;
the rest refreshes known useful games. Every inspection consumes the same cap.
"""
import json
from pathlib import Path
from datetime import timedelta
from history_quality import ensure_aware, parse_ts
from history_merge import atomic_write_text

class DiscoveryQueue:
    def __init__(self, path, now):
        self.path, self.now = Path(path), ensure_aware(now)
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        if not isinstance(self.state, dict):
            self.state = {}
        self.rows = self.state.get("events") or {}
        self.rows = {str(k):v for k,v in self.rows.items() if isinstance(v,dict)} if isinstance(self.rows,dict) else {}
        self.metrics = {}

    def select(self, events, budget, id_field="id", priority_key=None, ignored_markets=()):
        # Dedupe provider identity, never fuzzy teams. Keep input order as a tie
        # break (7k sorts by market count), then rotate using the persisted clock.
        unique = {}
        for event in events:
            if event.get(id_field) not in (None, ""):
                unique.setdefault(str(event[id_field]), event)
        items = list(unique.items())
        rank = {ident: i for i, (ident, _) in enumerate(items)}
        def age(item):
            rec = self.rows.get(item[0], {})
            dt = ensure_aware(parse_ts(rec.get("last_attempt")))
            priority = priority_key(item[1]) if priority_key else 0
            return (priority, dt.timestamp() if dt else float("-inf"), rank[item[0]])
        budget = max(0, int(budget))
        explore_budget = max(1, (budget + 3) // 4) if budget else 0
        ignored = set(ignored_markets)
        known = sorted([x for x in items if self.rows.get(x[0], {}).get("useful")
                        and (not ignored or set(self.rows[x[0]].get('markets') or []) - ignored)], key=age)
        chosen = known[:max(0, budget-explore_budget)]
        chosen_ids = {x[0] for x in chosen}
        # Exploration must rotate even when the priority tier never fits the
        # budget. Oldest/unseen first; league priority only breaks age ties.
        remaining = sorted([x for x in items if x[0] not in chosen_ids],
                           key=lambda item: (age(item)[1], age(item)[0], age(item)[2]))
        chosen.extend(remaining[:budget-len(chosen)])
        self.metrics = {"inventory":len(items),"budget":budget,"selected":len(chosen),
                        "not_selected":max(0,len(items)-len(chosen)),
                        "unseen":sum(x[0] not in self.rows for x in items),
                        "attempted":0,"succeeded":0,"useful":0,"errors":0,
                        "exploration_min_budget":explore_budget}
        return [event for _,event in chosen]

    def record(self, event_id, *, success, useful=False, markets=None):
        ident = str(event_id)
        rec = dict(self.rows.get(ident) or {})
        rec.update(last_attempt=self.now.isoformat(), attempts=int(rec.get("attempts") or 0)+1)
        if success:
            rec.update(last_success=self.now.isoformat(), useful=bool(useful), markets=sorted(set(markets or [])))
        self.rows[ident] = rec
        for field, value in (("attempted",1),("succeeded",int(success)),("useful",int(useful)),("errors",int(not success))):
            self.metrics[field] = self.metrics.get(field,0)+value

    def save(self):
        # Queue is ephemeral operational state, not historical odds. Expire old
        # identities after seven days so late/relisted markets get rediscovered.
        cutoff = self.now-timedelta(days=7)
        rows = {key:rec for key,rec in self.rows.items()
                if (ensure_aware(parse_ts(rec.get("last_attempt"))) or cutoff) >= cutoff}
        dates = [ensure_aware(parse_ts(r.get("last_attempt"))) for r in rows.values()]
        oldest = min((d for d in dates if d is not None), default=None)
        self.metrics["oldest_attempt_age_minutes"] = round((self.now-oldest).total_seconds()/60,1) if oldest else None
        atomic_write_text(self.path,json.dumps({"schema":1,"generated_at":self.now.isoformat(),
                          "events":rows,"metrics":self.metrics},ensure_ascii=False))
