"""Rule-aware card settlement. Unknown incidents are not silently red=1."""
import hashlib
import json
import math

def semantics_verified(record):
    return record.get("settlement_rule") in {"red2_incidents", "red2_no_reds", "red2_outcome_invariant"}

def number(value):
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) and x >= 0 and x.is_integer() else None
    except (TypeError, ValueError):
        return None

def outcome(total, line, side):
    if side not in ("over", "under"):
        raise ValueError("unsupported card side")
    if abs(total - line) < 1e-9:
        return None
    return (total > line) if side == "over" else (total < line)

def card_decision(row, line, side):
    evidence = {k: number(row.get(k)) for k in
                ("cards", "yellow_cards", "red_cards", "cards_r2", "reds_direct", "reds_second")}
    revision = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()[:20]
    r1, r2, reds = evidence["cards"], evidence["cards_r2"], evidence["red_cards"]
    result = {"settlement_revision": "cards-v2-" + revision,
              "result_r1": r1, "result_r2": r2, "result_bounds": None,
              "result_yellows": evidence["yellow_cards"], "result_reds": reds}
    if r2 is not None and r1 is not None and reds is not None and not r1 <= r2 <= r1 + reds:
        return {**result, "rule": "red2_inconsistent_result", "result": None,
                "won": None, "pending": True, "retryable": True}
    if r2 is not None:
        return {**result, "rule": "red2_incidents", "result": r2,
                "won": outcome(r2, line, side), "pending": False, "retryable": False}
    if r1 is not None and reds == 0:
        return {**result, "rule": "red2_no_reds", "result": r1,
                "won": outcome(r1, line, side), "pending": False, "retryable": False}
    if r1 is not None and reds is not None and reds.is_integer():
        low, high = r1, r1 + reds
        # O/U is monotonic: equal outcomes at both bounds prove the whole
        # interval; never allocate a range proportional to untrusted input.
        outcomes = {outcome(low, line, side), outcome(high, line, side)}
        result["result_bounds"] = [low, high]
        if len(outcomes) == 1:
            # Winner is provable, exact card total is not: never use a bound as
            # a numeric model target. Later incidents create a new revision.
            return {**result, "rule": "red2_outcome_invariant", "result": None,
                    "won": outcomes.pop(), "pending": False, "retryable": True}
    return {**result, "rule": "red2_pending_incidents", "result": None,
            "won": None, "pending": True, "retryable": True}
