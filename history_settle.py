# -*- coding: utf-8 -*-
"""Liquida o histórico e mantém backlog retryable/observável.

Resultados ausentes ou uma estatística ainda não publicada nunca viram um
estado terminal: ficam em ``pending_result`` e são tentados novamente em toda
execução. ``unavailable`` é reservado a mercados realmente sem mapeamento.
"""
from __future__ import annotations

import csv
import glob
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from rapidfuzz import fuzz

    def ratio(a, b):
        return fuzz.token_set_ratio(a, b)
except Exception:
    import difflib

    def ratio(a, b):
        return 100 * difflib.SequenceMatcher(None, a, b).ratio()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import norm_team, parse_history_key  # noqa: E402
from history_merge import atomic_write_text, merge_records  # noqa: E402

HIST = ROOT / "data" / "odds_history"
RES_AUTO = HIST / "results" / "results_auto.json"
RES_MANUAL = HIST / "results" / "manual_results.csv"
RES_STATUS = HIST / "results" / "settlement_status.json"
BRT = timezone(timedelta(hours=-3))

FIELD = {
    "Cartões": "cards",
    "Faltas": "fouls",
    "Finalizações": "shots",
    "Impedimentos": "offsides",
    "Laterais": "throw_ins",
    "Tiros de meta": "goal_kicks",
    "Escanteios": "corners",
    "Chutes no gol": "shots_on_goal",
    "Desarmes": "tackles",
}
RETRYABLE_STATUSES = {"closed", "pending_result"}
PENDING_STATUS = "pending_result"
RETRY_AUDIT_INTERVAL = timedelta(hours=6)


def nrm(value):
    return norm_team(value)


def _number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_results():
    """Carrega resultados automáticos e complementos manuais."""
    out = []
    if RES_AUTO.exists():
        try:
            data = json.loads(RES_AUTO.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict):
                        rec = dict(row)
                        rec["_source"] = "auto"
                        out.append(rec)
        except (OSError, ValueError):
            pass
    if RES_MANUAL.exists():
        try:
            with RES_MANUAL.open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    rec = {
                        "date": row.get("date"),
                        "home": row.get("home"),
                        "away": row.get("away"),
                        "_source": "manual",
                    }
                    for field in set(FIELD.values()):
                        rec[field] = _number((row.get(field) or "").strip())
                    out.append(rec)
        except (OSError, ValueError):
            pass
    for row in out:
        row["_h"] = nrm(row.get("home"))
        row["_a"] = nrm(row.get("away"))
    return out


def find_result(results, date, home, away, field=None):
    """Melhor jogo na data; com estatística disponível, manual precede auto."""
    candidates = []
    for row in results:
        if row.get("date") != date:
            continue
        score = min(ratio(home, row.get("_h") or ""), ratio(away, row.get("_a") or ""))
        if score >= 85:
            has_field = row.get(field) is not None if field else False
            is_manual = row.get("_source") == "manual"
            candidates.append((has_field, is_manual, score, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return candidates[0][3]


def consolidate_key_documents(documents):
    """Mescla chaves repetidas entre meses e escolhe o arquivo mais recente."""
    merged, owners = {}, {}
    duplicates = 0
    for path, keys in documents:
        for key, record in keys.items():
            if key.startswith("__") or not isinstance(record, dict):
                continue
            if key in merged:
                merged[key] = merge_records(merged[key], record)
                duplicates += 1
            else:
                merged[key] = dict(record)
            # O mês mais recente evita recriar a duplicata a cada novo ingest.
            owners[key] = path
    return merged, owners, duplicates


def persist_consolidated_documents(documents, merged, owners):
    """Remove cópias antigas e grava o estado global por rename atômico."""
    changed_files = 0
    for path, original in documents:
        updated = {
            key: value
            for key, value in original.items()
            if key.startswith("__") or not isinstance(value, dict)
        }
        for key, owner in owners.items():
            if owner == path:
                updated[key] = merged[key]
        if updated != original:
            atomic_write_text(path, json.dumps(updated, ensure_ascii=False))
            changed_files += 1
    return changed_files


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BRT)
        return parsed.astimezone(BRT)
    except (TypeError, ValueError):
        return None


def _mark_pending(record, reason, now):
    changed = (
        record.get("status") != PENDING_STATUS
        or record.get("settlement_reason") != reason
        or record.get("settlement_retryable") is not True
    )
    last_attempt = _parse_dt(record.get("settlement_last_attempt"))
    audit_due = last_attempt is None or now - last_attempt >= RETRY_AUDIT_INTERVAL
    if changed or audit_due:
        record["status"] = PENDING_STATUS
        record["result"] = None
        record["won"] = None
        record["settlement_reason"] = reason
        record["settlement_retryable"] = True
        record["settlement_last_attempt"] = now.isoformat()
        record["settlement_attempts"] = int(record.get("settlement_attempts") or 0) + 1
        return True
    return False


def settle_one(key, record, results, now):
    """Tenta liquidar uma key. Retorna ``(outcome, changed, clv_row)``."""
    meta = parse_history_key(key)
    market = meta.get("mercado")
    field = FIELD.get(market)
    if not field:
        changed = record.get("status") != "unavailable"
        record["status"] = "unavailable"
        record["result"] = None
        record["won"] = None
        record["settlement_reason"] = "unsupported_market"
        record["settlement_retryable"] = False
        record["settlement_last_attempt"] = now.isoformat()
        record["settlement_attempts"] = int(record.get("settlement_attempts") or 0) + 1
        return "unavailable", changed, None

    try:
        line = float(meta.get("linha"))
    except (TypeError, ValueError):
        changed = _mark_pending(record, "invalid_line", now)
        return "pending", changed, None

    game_date = (record.get("kickoff") or "")[:10] or meta.get("day") or ""
    home = record.get("home_norm") or nrm(record.get("home_raw") or meta.get("hn") or "")
    away = record.get("away_norm") or nrm(record.get("away_raw") or meta.get("an") or "")
    result_row = find_result(results, game_date, home, away, field=field)
    if result_row is None:
        changed = _mark_pending(record, "game_not_in_results", now)
        return "pending", changed, None

    result = _number(result_row.get(field))
    if result is None:
        changed = _mark_pending(record, f"stat_missing:{field}", now)
        return "pending", changed, None

    side = meta.get("lado") or "over"
    record["result"] = result
    if abs(result - line) < 1e-9:
        record["won"] = None
    else:
        over_won = result > line
        record["won"] = over_won if side == "over" else not over_won
    if record.get("open_odd") and record.get("close_odd"):
        record["clv_pct"] = round(
            (float(record["open_odd"]) / float(record["close_odd"]) - 1) * 100, 2
        )
        record["beat_close"] = record["clv_pct"] > 0
    record["status"] = "settled"
    record["settled_at"] = now.isoformat()
    record["settlement_last_attempt"] = now.isoformat()
    record["settlement_attempts"] = int(record.get("settlement_attempts") or 0) + 1
    record["settlement_reason"] = "settled"
    record["settlement_retryable"] = False
    record["settlement_source"] = result_row.get("_source") or "auto"
    clv_row = {
        "key": key,
        "casa": meta.get("casa") or key.split("|")[0],
        "mercado": market,
        "linha": line,
        "lado": side,
        "open_odd": record.get("open_odd"),
        "close_odd": record.get("close_odd"),
        "clv_pct": record.get("clv_pct"),
        "beat_close": record.get("beat_close"),
        "result": result,
        "won": record.get("won"),
        "kickoff": record.get("kickoff") or game_date,
        "sofa_id": record.get("sofa_id"),
    }
    return "settled", True, clv_row


def _age_bucket(kickoff, now):
    parsed = _parse_dt(kickoff)
    if parsed is None:
        return "unknown", None
    age_days = (now - parsed).total_seconds() / 86400
    if age_days < 0:
        return "not_started", age_days
    if age_days < 1:
        return "0-24h", age_days
    if age_days < 3:
        return "1-3d", age_days
    if age_days < 7:
        return "3-7d", age_days
    if age_days < 30:
        return "7-30d", age_days
    return "30d+", age_days


def build_settlement_status(records, results, now):
    """Resumo operacional por mercado, status, motivo e idade do backlog."""
    total_status = Counter()
    by_market = defaultdict(
        lambda: {
            "total": 0,
            "status": Counter(),
            "pending_age": Counter(),
            "pending_reasons": Counter(),
        }
    )
    backlog_age, backlog_reasons = Counter(), Counter()
    backlog_samples = []

    for key, record in records:
        meta = parse_history_key(key)
        market = meta.get("mercado") or "unknown"
        status = record.get("status") or "unknown"
        total_status[status] += 1
        market_row = by_market[market]
        market_row["total"] += 1
        market_row["status"][status] += 1
        if status == PENDING_STATUS:
            bucket, age_days = _age_bucket(record.get("kickoff"), now)
            reason = record.get("settlement_reason") or "unknown"
            market_row["pending_age"][bucket] += 1
            market_row["pending_reasons"][reason] += 1
            backlog_age[bucket] += 1
            backlog_reasons[reason] += 1
            backlog_samples.append(
                {
                    "key": key,
                    "market": market,
                    "kickoff": record.get("kickoff"),
                    "age_days": round(age_days, 2) if age_days is not None else None,
                    "reason": reason,
                    "attempts": record.get("settlement_attempts") or 0,
                }
            )

    coverage = {}
    for field in sorted(set(FIELD.values())):
        available = sum(1 for row in results if row.get(field) is not None)
        coverage[field] = {"available": available, "missing": len(results) - available}

    def plain_market(row):
        return {
            "total": row["total"],
            "status": dict(row["status"]),
            "pending_age": dict(row["pending_age"]),
            "pending_reasons": dict(row["pending_reasons"]),
        }

    backlog_samples.sort(
        key=lambda row: row.get("age_days") if row.get("age_days") is not None else -1,
        reverse=True,
    )
    return {
        "generated_at": now.isoformat(),
        "results_rows": len(results),
        "result_field_coverage": coverage,
        "totals_by_status": dict(total_status),
        "by_market": {
            market: plain_market(row) for market, row in sorted(by_market.items())
        },
        "backlog": {
            "total": total_status.get(PENDING_STATUS, 0),
            "age": dict(backlog_age),
            "reasons": dict(backlog_reasons),
            "oldest_samples": backlog_samples[:25],
        },
    }


def _append_clv(rows):
    if not rows:
        return
    (HIST / "clv").mkdir(parents=True, exist_ok=True)
    by_month = defaultdict(list)
    for row in rows:
        kickoff = str(row.get("kickoff") or "")
        month = kickoff[:7] if len(kickoff) >= 7 else datetime.now(BRT).strftime("%Y-%m")
        by_month[month].append(row)
    for month, month_rows in by_month.items():
        with (HIST / "clv" / f"{month}.jsonl").open("a", encoding="utf-8") as handle:
            for row in month_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    now = datetime.now(BRT)
    results = load_results()
    print(f"[settle] resultados disponíveis: {len(results):,}")
    outcomes = Counter()
    clv_rows = []
    paths = [Path(name) for name in sorted(glob.glob(str(HIST / "keys" / "*.json")))]
    documents = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    merged, owners, duplicates = consolidate_key_documents(documents)

    for key, record in merged.items():
        current_status = record.get("status")
        retryable = current_status in RETRYABLE_STATUSES or (
            current_status == "unavailable" and record.get("settlement_retryable") is not False
        )
        if retryable:
            outcome, _record_changed, clv_row = settle_one(key, record, results, now)
            outcomes[outcome] += 1
            if clv_row:
                clv_rows.append(clv_row)

    changed_files = persist_consolidated_documents(documents, merged, owners)
    all_records = list(merged.items())

    _append_clv(clv_rows)
    status = build_settlement_status(all_records, results, now)
    RES_STATUS.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(RES_STATUS, json.dumps(status, ensure_ascii=False, indent=2))
    print(
        f"[settle] {outcomes['settled']:,} liquidadas · "
        f"{outcomes['pending']:,} em retry · {outcomes['unavailable']:,} sem mapeamento"
    )
    if duplicates:
        print(f"[settle] {duplicates:,} duplicatas mensais consolidadas · {changed_files} arquivos atualizados")
    for market, row in status["by_market"].items():
        pending = row["status"].get(PENDING_STATUS, 0)
        if pending:
            ages = ", ".join(f"{name}={count}" for name, count in row["pending_age"].items())
            print(f"  [backlog] {market}: {pending} · {ages}")
    print(f"[settle] observabilidade -> {RES_STATUS}")


if __name__ == "__main__":
    main()
