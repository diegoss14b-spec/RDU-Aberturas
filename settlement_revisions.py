"""Append-only revisions are evidence, not additional bets."""
def latest_revisions(rows):
    """Collapse append-order revisions per key for P&L/model analyses.

    Ledger writers append every revision once, after the original. Historical
    unrevisioned rows stay intact on disk. Consumers must not sum raw revisions.
    """
    current = {}
    for row in rows:
        if row.get("key"):
            current[row["key"]] = row
    return list(current.values())
