"""Observed time is source evidence, never a processing-clock fallback."""
import hashlib
from datetime import timedelta
from history_quality import ensure_aware, parse_ts

SCHEMA = 2

def observation_meta(event, raw_line):
    observed = ensure_aware(parse_ts(event.get("captured_at")))
    return {
        "observed_at": observed.isoformat() if observed else None,
        "snapshot_row_sha256": hashlib.sha256(raw_line.encode("utf-8")).hexdigest(),
        "timestamp_source": "source_captured_at" if observed else "unknown",
    }

def observation_reason(event, now, record=None):
    observed = ensure_aware(parse_ts(event.get("observed_at")))
    if observed is None:
        return "missing_observed_at"
    if observed > ensure_aware(now) + timedelta(seconds=60):
        return "future_observed_at"
    if record:
        previous = ensure_aware(parse_ts(record.get("last_seen_observed_at") or record.get("last_observed_at")))
        if previous and observed <= previous:
            return "duplicate_or_out_of_order_observation"
    return None

def close_age_band(record):
    ko = ensure_aware(parse_ts(record.get("kickoff")))
    observed = ensure_aware(parse_ts(record.get("close_observed_at")))
    if not ko or not observed or record.get("close_time_verified") is not True:
        return "unknown"
    minutes = (ko - observed).total_seconds() / 60
    if minutes < 0:
        return "post_kickoff"
    for limit in (15, 30, 60):
        if minutes <= limit:
            return "within_%dm" % limit
    return "older_than_60m"
