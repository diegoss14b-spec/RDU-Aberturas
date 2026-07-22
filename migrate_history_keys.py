# -*- coding: utf-8 -*-
"""Migra identidades legadas do histórico para ``sofa:{id}``.

A migração é segura para ser repetida. Ela consolida keys, estado da main line e
ticks, preservando abertura, fechamento, resultado e metadados de qualidade.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from canonical import (  # noqa: E402
    gscore,
    history_key,
    load_sofa_fixtures,
    norm_team,
    parse_history_key,
    resolve_fixture,
    unify_gids,
)
from history_merge import atomic_write_text, merge_latest_state, merge_records  # noqa: E402

KEYS = ROOT / "data" / "odds_history" / "keys"
TICKS = ROOT / "data" / "odds_history" / "ticks"


def merge_rec(a, b):
    """Alias compatível com versões anteriores/tests externos."""
    return merge_records(a, b)


def _resolve_legacy(home, away, start, day, league, fixtures):
    """Resolve uma identidade antiga sem aceitar empate ambíguo por data."""
    if start:
        return resolve_fixture(home, away, start, league=league, fixtures=fixtures)

    hn, an = norm_team(home), norm_team(away)
    scored = []
    for fixture in fixtures:
        if fixture.get("day_brt") != day:
            continue
        fhn = fixture.get("_hn") or norm_team(fixture.get("home"))
        fan = fixture.get("_an") or norm_team(fixture.get("away"))
        scored.append((gscore(hn, an, fhn, fan), fixture))
    scored.sort(key=lambda item: item[0], reverse=True)
    unique = (
        scored
        and scored[0][0] >= 88
        and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 8)
    )
    if unique:
        return resolve_fixture(
            home, away, scored[0][1].get("start_ts"), league=league, fixtures=fixtures
        )
    return resolve_fixture(home, away, day or None, league=league, fixtures=[])


def _migrate_main_lines(store, fixtures):
    new, merges = {}, 0
    for old_key, state in (store or {}).items():
        parts = str(old_key).split("|")
        if len(parts) < 3 or not isinstance(state, dict):
            new[old_key] = state
            continue
        casa, mercado = parts[0], parts[-1]
        gid_parts = parts[1:-1]
        if len(gid_parts) == 1 and gid_parts[0].startswith("sofa:"):
            new_key = old_key
        elif len(gid_parts) >= 3:
            day, home, away = gid_parts[0], gid_parts[1], gid_parts[2]
            identity = _resolve_legacy(home, away, None, day, "", fixtures)
            gid = (
                f"sofa:{identity['sofa_id']}"
                if identity.get("sofa_id")
                else "|".join(gid_parts)
            )
            new_key = f"{casa}|{gid}|{mercado}"
        else:
            new_key = old_key
        if new_key in new:
            new[new_key] = merge_latest_state(new[new_key], state)
            merges += 1
        else:
            new[new_key] = dict(state)
    return new, merges


def migrate_keys_dict(keys, fixtures):
    """Versão pura e idempotente, reutilizada pelo ingest diário."""
    new = {}
    stats = {"sofa": 0, "legacy": 0, "merges": 0, "main_line_merges": 0}
    main_store = keys.get("__main_lines__") if isinstance(keys, dict) else None

    for old_key, original in (keys or {}).items():
        if old_key == "__main_lines__":
            continue
        if old_key.startswith("__") or not isinstance(original, dict):
            new[old_key] = original
            continue

        rec = dict(original)
        meta = parse_history_key(old_key)
        casa = meta.get("casa") or old_key.split("|")[0]
        mercado, linha, lado = meta.get("mercado"), meta.get("linha"), meta.get("lado")
        if not mercado or linha is None or not lado:
            new[old_key] = rec
            continue

        if meta.get("format") == "sofa":
            rec["sofa_id"] = meta.get("sofa_id")
            new_key = old_key
            stats["sofa"] += 1
        else:
            home = rec.get("home_raw") or meta.get("hn") or ""
            away = rec.get("away_raw") or meta.get("an") or ""
            day = meta.get("day") or (rec.get("kickoff") or "")[:10]
            identity = _resolve_legacy(
                home,
                away,
                rec.get("kickoff"),
                day,
                rec.get("league") or "",
                fixtures,
            )
            resolved_day = identity["day"] if identity.get("day") != "?" else day
            new_key = history_key(
                casa,
                resolved_day,
                identity["hn"],
                identity["an"],
                mercado,
                linha,
                lado,
                sofa_id=identity.get("sofa_id"),
            )
            rec["home_norm"], rec["away_norm"] = identity["hn"], identity["an"]
            if identity.get("sofa_id"):
                rec["sofa_id"] = str(identity["sofa_id"])
                rec["match_method"] = identity.get("match_method")
                rec["match_confidence"] = identity.get("match_confidence")
                rec["merged_from_keys"] = sorted(
                    set((rec.get("merged_from_keys") or []) + [old_key])
                )
                stats["sofa"] += 1
            else:
                stats["legacy"] += 1

        if new_key in new:
            new[new_key] = merge_records(new[new_key], rec)
            stats["merges"] += 1
        else:
            new[new_key] = rec

    migrated_main, main_merges = _migrate_main_lines(main_store or {}, fixtures)
    if main_store is not None or migrated_main:
        new["__main_lines__"] = migrated_main
    stats["main_line_merges"] = main_merges
    return new, stats


def _kick_epoch(rec):
    """Epoch (s) do kickoff do registro, ou None."""
    try:
        from history_quality import parse_ts, ensure_aware
        dt = ensure_aware(parse_ts(rec.get("kickoff")))
        return int(dt.timestamp()) if dt else None
    except Exception:
        return None


def _gid_of(key_meta, rec):
    """Identidade de jogo (gid) de uma key + registro."""
    if key_meta.get("format") == "sofa":
        sid = rec.get("sofa_id") or key_meta.get("sofa_id")
        return f"sofa:{sid}", (rec.get("kickoff") or "")[:10], \
            rec.get("home_norm") or "", rec.get("away_norm") or ""
    day = key_meta.get("day") or (rec.get("kickoff") or "")[:10]
    hn = key_meta.get("hn") or rec.get("home_norm") or ""
    an = key_meta.get("an") or rec.get("away_norm") or ""
    return f"{day}|{hn}|{an}", day, hn, an


def unify_keys_dict(keys):
    """Dedup de CONFRONTOS: junta keys do mesmo jogo gravado com nomes/dia
    diferentes por casas diferentes (fuzzy ±1 dia, ver canonical.unify_gids).

    Retorna (keys_novo, alias {gid_antigo: gid_canônico}, stats). Idempotente.
    """
    games = {}
    for k, v in (keys or {}).items():
        if k.startswith("__") or not isinstance(v, dict):
            continue
        meta = parse_history_key(k)
        if meta.get("format") not in ("sofa", "legacy"):
            continue
        gid, day, hn, an = _gid_of(meta, v)
        g = games.setdefault(gid, {"day": day, "hn": hn, "an": an, "n": 0,
                                   "sofa": gid.startswith("sofa:"),
                                   "kick_ts": None})
        g["n"] += 1
        if not g["day"] and day:
            g["day"] = day
        if not g["hn"] and hn:
            g["hn"], g["an"] = hn, an
        ke = _kick_epoch(v)
        if ke and (g["kick_ts"] is None or ke < g["kick_ts"]):
            g["kick_ts"] = ke  # kickoff mais cedo observado do confronto

    alias = unify_gids(games)
    stats = {"gid_merges": len(alias), "key_merges": 0, "main_line_merges": 0}
    if not alias:
        return keys, alias, stats

    new = {}
    main_store = keys.get("__main_lines__") if isinstance(keys, dict) else None
    for k, v in keys.items():
        if k == "__main_lines__":
            continue
        if k.startswith("__") or not isinstance(v, dict):
            new[k] = v
            continue
        meta = parse_history_key(k)
        if meta.get("format") not in ("sofa", "legacy"):
            new[k] = v
            continue
        gid, _, _, _ = _gid_of(meta, v)
        canon = alias.get(gid)
        if not canon:
            if k in new:
                new[k] = merge_records(new[k], v)
                stats["key_merges"] += 1
            else:
                new[k] = v
            continue
        casa = meta.get("casa") or k.split("|")[0]
        mercado, linha, lado = meta["mercado"], meta["linha"], meta["lado"]
        rec = dict(v)
        rec["merged_from_keys"] = sorted(set((rec.get("merged_from_keys") or []) + [k]))
        if canon.startswith("sofa:"):
            rec["sofa_id"] = canon.split(":", 1)[1]
            new_key = f"{casa}|{canon}|{mercado}|{linha}|{lado}"
        else:
            cday, chn, can_ = canon.split("|")
            rec["home_norm"], rec["away_norm"] = chn, can_
            new_key = f"{casa}|{cday}|{chn}|{can_}|{mercado}|{linha}|{lado}"
        if new_key in new:
            new[new_key] = merge_records(new[new_key], rec)
            stats["key_merges"] += 1
        else:
            new[new_key] = rec

    # __main_lines__: casa|<gid>|mercado — segue o mesmo alias
    if main_store is not None:
        migrated_main = {}
        for old_key, state in (main_store or {}).items():
            parts = str(old_key).split("|")
            new_key = old_key
            if len(parts) >= 3 and isinstance(state, dict):
                casa, mercado = parts[0], parts[-1]
                gid = "|".join(parts[1:-1])
                canon = alias.get(gid)
                if canon:
                    new_key = f"{casa}|{canon}|{mercado}"
            if new_key in migrated_main and isinstance(state, dict):
                migrated_main[new_key] = merge_latest_state(migrated_main[new_key], state)
                stats["main_line_merges"] += 1
            else:
                migrated_main[new_key] = state
        new["__main_lines__"] = migrated_main
    return new, alias, stats


def migrate_file(path: Path, fixtures):
    original = path.read_text(encoding="utf-8")
    keys = json.loads(original)
    new, stats = migrate_keys_dict(keys, fixtures)
    # dedup de confrontos (mesmo jogo com grafias/dia diferentes entre casas)
    new, _alias, ustats = unify_keys_dict(new)
    backup = path.with_suffix(path.suffix + ".bak_pre_migrate")
    if backup.exists():
        backup.unlink()
    migrated = json.dumps(new, ensure_ascii=False)
    if migrated != original:
        atomic_write_text(path, migrated)
    print(
        f"[migrate] {path.name}: {len(keys)} -> {len(new)} keys · "
        f"sofa={stats['sofa']} · leg={stats['legacy']} · "
        f"merges={stats['merges']} · main_merges={stats['main_line_merges']} · "
        f"dedup jogos={ustats['gid_merges']} (keys unidas={ustats['key_merges']})"
    )
    return len(new)


def _tick_signature(row):
    fields = (
        "ts",
        "kind",
        "casa",
        "gid",
        "mercado",
        "linha",
        "lado",
        "odd",
        "linha_from",
        "linha_to",
    )
    return tuple(row.get(field) for field in fields)


def migrate_tick_rows(rows, fixtures):
    """Canoniza gids e remove só ticks logicamente idênticos."""
    out, positions = [], {}
    stats = {"sofa": 0, "deduped": 0}
    for original in rows:
        row = dict(original)
        sofa_id = row.get("sofa_id")
        gid = str(row.get("gid") or "")
        if not sofa_id and gid.startswith("sofa:"):
            sofa_id = gid.split(":", 1)[1]
        if not sofa_id:
            parts = gid.split("|") if gid else []
            day = row.get("djogo") or (parts[0] if len(parts) >= 3 else "")
            home = row.get("home") or (parts[1] if len(parts) >= 3 else "")
            away = row.get("away") or (parts[2] if len(parts) >= 3 else "")
            if day and home and away:
                identity = _resolve_legacy(
                    home,
                    away,
                    row.get("kickoff"),
                    day,
                    row.get("league") or "",
                    fixtures,
                )
                sofa_id = identity.get("sofa_id")
                if sofa_id:
                    row["home"], row["away"] = identity["hn"], identity["an"]
        if sofa_id:
            sofa_id = str(sofa_id)
            row["sofa_id"] = sofa_id
            row["gid"] = f"sofa:{sofa_id}"
            stats["sofa"] += 1

        signature = _tick_signature(row)
        if signature in positions:
            current = out[positions[signature]]
            for field, value in row.items():
                if current.get(field) in (None, "") and value not in (None, ""):
                    current[field] = value
            stats["deduped"] += 1
        else:
            positions[signature] = len(out)
            out.append(row)
    return out, stats


def migrate_tick_file(path: Path, fixtures):
    original = path.read_text(encoding="utf-8")
    lines = [line for line in original.splitlines() if line.strip()]
    try:
        rows = [json.loads(line) for line in lines]
    except (TypeError, ValueError) as exc:
        print(f"[migrate] {path.name}: tick JSON inválido; preservado ({exc})")
        return len(lines)
    new, stats = migrate_tick_rows(rows, fixtures)
    backup = path.with_suffix(path.suffix + ".bak_pre_migrate")
    if backup.exists():
        backup.unlink()
    migrated = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in new)
    if migrated != original:
        atomic_write_text(path, migrated)
    print(
        f"[migrate] {path.name}: {len(rows)} -> {len(new)} ticks · "
        f"sofa={stats['sofa']} · dedup={stats['deduped']}"
    )
    return len(new)


def main():
    fixtures = load_sofa_fixtures()
    removed_backups = 0
    for directory in (KEYS, TICKS):
        for backup in directory.glob("*.bak_pre_migrate"):
            backup.unlink()
            removed_backups += 1
    if removed_backups:
        print(f"[migrate] removidos {removed_backups} backups legados não versionáveis")
    print(f"[migrate] fixtures sofa={len(fixtures)}")
    files = sorted(KEYS.glob("*.json"))
    if not files:
        print("[migrate] nenhum keys/*.json")
    for path in files:
        migrate_file(path, fixtures)
    for path in sorted(TICKS.glob("*.jsonl")):
        migrate_tick_file(path, fixtures)


if __name__ == "__main__":
    main()
