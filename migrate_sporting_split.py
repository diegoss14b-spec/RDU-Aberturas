# -*- coding: utf-8 -*-
"""migrate_sporting_split.py — separa identidades IMPURAS por sofa_id (caso Sporting).

Contexto (brief 22/07 §6): o matcher one_side casou "Sporting Cristal x RB
Bragantino" (Sul-Americana) no evento MLS sofa:15171608 ("Sporting Kansas City x
Minnesota United") — 155 chaves e ~1.839 movimentos contaminados. Este script:

  1. varre o banco (keys/*.json) com ``canonical.sofa_purity`` e encontra os
     sofa_id com pares crus incompatíveis;
  2. reatribui cada chave POR EVIDÊNCIA (nomes crus preservados na chave, casa,
     kickoff): dono do id fica; o outro jogo vai pro id Sofa correto comprovado
     no arquivo de evidência; o que não se prova vai pra QUARENTENA (gid legado
     + marcador ``quarantine``) — nunca associação inventada;
  3. reagrupa os ticks (movimentos) preservando timestamp/casa/mercado/linha/
     lado/odd — nada é apagado, contagem antes = depois;
  4. migra/limpa o estado de ``__main_lines__``;
  5. grava relatório origem→destino em data/odds_history/_migration_reports/.

Uso:
  python3 migrate_sporting_split.py --dry                # só relata
  python3 migrate_sporting_split.py                      # aplica no repo
  python3 migrate_sporting_split.py --root /copia/isolada
Idempotente: na 2ª execução o banco já está puro → 0 mudanças.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from canonical import (  # noqa: E402
    gscore, norm_team, parse_history_key, sofa_purity,
)
from history_merge import atomic_write_text, merge_records  # noqa: E402
from history_quality import ensure_aware, parse_ts  # noqa: E402

BRT = timezone(timedelta(hours=-3))
EVIDENCE_DEFAULT = "data/fixtures/_sporting_split_evidence.json"
S_MIN = 80          # semelhança mínima do par cru com a identidade candidata
S_MARGIN = 5        # margem mínima sobre a alternativa
KICK_TOL_H = 12     # kickoff do id correto tem que estar a ≤ isto do kickoff da chave


def line_key(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(v)) if v.is_integer() else str(v)


def load_evidence(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def classify(rec, owner, correct):
    """→ ('keep'|'move'|'quarantine', s_owner, s_correct)."""
    hr = norm_team(rec.get("home_raw") or rec.get("home_norm") or "")
    ar = norm_team(rec.get("away_raw") or rec.get("away_norm") or "")
    if not hr or not ar:
        return "quarantine", 0.0, 0.0
    s_own = gscore(hr, ar, owner["hn"], owner["an"]) if owner else 0.0
    s_cor = gscore(hr, ar, correct["hn"], correct["an"]) if correct else 0.0
    if s_own >= S_MIN and s_own >= s_cor + S_MARGIN:
        return "keep", s_own, s_cor
    if correct and s_cor >= S_MIN and s_cor >= s_own + S_MARGIN:
        return "move", s_own, s_cor
    return "quarantine", s_own, s_cor


def kick_compatible(rec, correct):
    """kickoff da chave e do evento correto a ≤ KICK_TOL_H horas."""
    if not correct or not correct.get("start_ts"):
        return False
    kt = ensure_aware(parse_ts(rec.get("kickoff")))
    if not kt:
        return True  # sem kickoff gravado: nomes decidem
    try:
        delta_h = abs(kt.timestamp() - int(correct["start_ts"])) / 3600.0
    except Exception:
        return True
    return delta_h <= KICK_TOL_H


_SETTLE_FIELDS = ("result", "won", "clv_pct", "beat_close", "settled_at",
                  "settlement_reason", "settlement_source", "settlement_attempts",
                  "settlement_last_attempt", "settlement_retryable")


def _reset_settlement(rec, now_iso):
    """Chave movida/quarentenada que estava settled foi liquidada com o resultado
    do JOGO ERRADO — zera a liquidação (o settle re-roda com a identidade nova).
    Nunca inventa resultado: status volta pra 'closed' (ou 'open' se nunca fechou)."""
    if rec.get("status") != "settled":
        return False
    for f in _SETTLE_FIELDS:
        rec.pop(f, None)
    rec["result"] = None
    rec["won"] = None
    rec["clv_pct"] = None
    rec["status"] = "closed" if rec.get("close_odd") else "open"
    rec["settlement_reset"] = {"reason": "identidade corrigida na migração", "ts": now_iso}
    return True


def migrate_root(root: Path, dry: bool, target_sid=None, evidence_path=None,
                 all_impure=False):
    keys_dir = root / "data" / "odds_history" / "keys"
    ticks_dir = root / "data" / "odds_history" / "ticks"
    rep_dir = root / "data" / "odds_history" / "_migration_reports"
    now_iso = datetime.now(BRT).strftime("%Y-%m-%dT%H:%M:%S%z")

    ev = load_evidence(root / (evidence_path or EVIDENCE_DEFAULT))
    correct = owner_hint = None
    ev_target = None
    if ev:
        e = ev.get("event") or {}
        c = ev.get("collision") or {}
        if e.get("sofa_id"):
            correct = {
                "sofa_id": int(e["sofa_id"]),
                "home": e.get("home"), "away": e.get("away"),
                "hn": norm_team(e.get("home")), "an": norm_team(e.get("away")),
                "start_ts": e.get("start_ts"),
            }
        if c.get("sofa_id"):
            ev_target = str(c["sofa_id"])
            owner_hint = {
                "home": c.get("home"), "away": c.get("away"),
                "hn": norm_team(c.get("home")), "an": norm_team(c.get("away")),
            }
    if target_sid is None and not all_impure:
        target_sid = ev_target

    report = {
        "ts": now_iso, "dry": dry, "root": str(root),
        "target_sofa_id": target_sid,
        "correct_event": correct, "owner": owner_hint,
        "files": [], "impure_found": {}, "totals": {},
    }

    # ------------------------------------------------------------- varredura
    all_keys = {}
    per_file = {}
    for f in sorted(keys_dir.glob("*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[split] pulei {f.name}: {type(exc).__name__}")
            continue
        per_file[f] = raw
        for k, v in raw.items():
            if not str(k).startswith("__") and isinstance(v, dict):
                all_keys[k] = v

    purity = sofa_purity(all_keys)
    impure = {sid: rep for sid, rep in purity.items() if rep["impure"]}
    report["impure_found"] = {sid: rep["clusters"] for sid, rep in impure.items()}
    if not impure:
        print("[split] banco puro — nada a migrar (idempotência OK).")
        report["totals"] = {"keys_moved": 0, "keys_quarantined": 0, "keys_kept": 0,
                            "ticks_moved": 0, "changes": 0}
        _write_report(rep_dir, report, dry)
        return report

    sids = sorted(impure)
    if target_sid and target_sid in impure:
        pass
    elif target_sid:
        print(f"[split] alvo sofa:{target_sid} já está puro; impuros restantes: {sids}")
    print(f"[split] sofa_ids impuros: {sids}")

    tot_keep = tot_move = tot_quar = tot_obs = tot_moves = tot_resets = 0
    assign = {}          # (casa, mercado, linha_str, lado) -> gid destino (por sid)
    assign_by_sid = {}
    changes = 0

    def _tot(d):
        n = obs = mv = 0
        for kk, vv in d.items():
            if str(kk).startswith("__") or not isinstance(vv, dict):
                continue
            n += 1
            obs += int(vv.get("n_obs") or 0)
            mv += int(vv.get("n_moves") or 0)
        return n, obs, mv

    for f, raw in per_file.items():
        new = {}
        file_changes = 0
        fmoves = {"keep": 0, "move": 0, "quarantine": 0}
        main_store = raw.get("__main_lines__")
        for k, v in raw.items():
            if str(k).startswith("__") or not isinstance(v, dict):
                if k != "__main_lines__":
                    new[k] = v
                continue
            meta = parse_history_key(k)
            sid = str(v.get("sofa_id") or meta.get("sofa_id") or "")
            if sid not in impure or (target_sid and sid != str(target_sid)):
                new[k] = merge_records(new[k], v) if k in new else v
                continue

            # identidade dona do id: evidência > nomes com match 'pair'
            owner = owner_hint if (ev_target and sid == ev_target) else None
            if owner is None:
                owner = _owner_from_pairs(all_keys, sid)
            cor = correct if (ev_target and sid == ev_target) else None

            action, s_own, s_cor = classify(v, owner, cor)
            if action == "move" and not kick_compatible(v, cor):
                action = "quarantine"

            casa = meta.get("casa") or k.split("|")[0]
            mercado, linha, lado = meta.get("mercado"), meta.get("linha"), meta.get("lado")
            rec = dict(v)
            old_gid = f"sofa:{sid}"

            if action == "keep":
                fmoves["keep"] += 1
                tot_keep += 1
                new[k] = merge_records(new[k], rec) if k in new else rec
                continue

            if action == "move":
                new_gid = f"sofa:{cor['sofa_id']}"
                rec["sofa_id"] = cor["sofa_id"]
                rec["home_norm"], rec["away_norm"] = cor["hn"], cor["an"]
                rec["match_method"] = "migrated_evidence"
                rec["match_confidence"] = 95
                rec["match_evidence"] = {
                    "method": "migrated_evidence", "s_owner": round(s_own, 1),
                    "s_correct": round(s_cor, 1), "migrated_from": old_gid,
                    "migration": "sporting_split", "ts": now_iso,
                }
                rec.pop("quarantine", None)
                new_key = f"{casa}|{new_gid}|{mercado}|{linha}|{lado}"
                fmoves["move"] += 1
                tot_move += 1
            else:
                day = (rec.get("kickoff") or "")[:10] or "?"
                hn = norm_team(rec.get("home_raw") or rec.get("home_norm") or "")
                an = norm_team(rec.get("away_raw") or rec.get("away_norm") or "")
                new_gid = f"{day}|{hn}|{an}"
                rec.pop("sofa_id", None)
                rec["home_norm"], rec["away_norm"] = hn, an
                rec["match_method"] = "quarantined"
                rec["match_confidence"] = 0
                rec["quarantine"] = {
                    "from": old_gid, "reason": "identidade ambígua na separação",
                    "s_owner": round(s_own, 1), "s_correct": round(s_cor, 1),
                    "ts": now_iso,
                }
                new_key = f"{casa}|{day}|{hn}|{an}|{mercado}|{linha}|{lado}"
                fmoves["quarantine"] += 1
                tot_quar += 1

            rec["merged_from_keys"] = sorted(set((rec.get("merged_from_keys") or []) + [k]))
            if _reset_settlement(rec, now_iso):
                tot_resets += 1
            tot_obs += int(rec.get("n_obs") or 0)
            tot_moves += int(rec.get("n_moves") or 0)
            new[new_key] = merge_records(new[new_key], rec) if new_key in new else rec
            a = assign.setdefault((casa, mercado, line_key(linha), lado), {})
            a[old_gid] = new_gid
            assign_by_sid.setdefault(sid, {}).setdefault((casa, mercado), set()).add(new_gid)
            file_changes += 1

        # __main_lines__: segue a chave quando o destino é único; ambíguo = remove
        if isinstance(main_store, dict):
            migrated_main = {}
            ml_moved = ml_dropped = 0
            for mk, state in main_store.items():
                parts = str(mk).split("|")
                new_mk = mk
                if len(parts) >= 3:
                    casa, mercado = parts[0], parts[-1]
                    gid = "|".join(parts[1:-1])
                    sid = gid.replace("sofa:", "", 1) if gid.startswith("sofa:") else None
                    if sid and sid in impure and (not target_sid or sid == str(target_sid)):
                        dests = assign_by_sid.get(sid, {}).get((casa, mercado))
                        if dests and len(dests) == 1:
                            new_mk = f"{casa}|{next(iter(dests))}|{mercado}"
                            ml_moved += 1
                        elif dests:
                            ml_dropped += 1  # misto: re-semeia no próximo ingest
                            continue
                migrated_main[new_mk] = state
            new["__main_lines__"] = migrated_main
            if ml_moved or ml_dropped:
                file_changes += ml_moved + ml_dropped
                fmoves["main_lines"] = {"moved": ml_moved, "dropped": ml_dropped}
        elif main_store is not None:
            new["__main_lines__"] = main_store

        n_before, obs_before, mv_before = _tot(raw)
        n_after, obs_after, mv_after = _tot(new)
        # INVARIANTE: nenhuma observação/movimento se perde — re-key só move ou
        # funde (merge_records SOMA contadores). Chave a menos = fusão com chave
        # legada já existente do MESMO confronto (dedup, não perda).
        assert obs_after == obs_before, \
            f"{f.name}: n_obs mudou ({obs_before} → {obs_after}) — ABORTADO, nada gravado"
        assert mv_after == mv_before, \
            f"{f.name}: n_moves mudou ({mv_before} → {mv_after}) — ABORTADO, nada gravado"
        report["files"].append({"file": f.name, "keys_before": n_before,
                                "keys_after": n_after,
                                "n_obs": obs_before, "n_moves": mv_before,
                                "invariantes_ok": True, **fmoves})
        if file_changes and not dry:
            atomic_write_text(f, json.dumps(new, ensure_ascii=False))
        changes += file_changes
        print(f"[split] {f.name}: keep={fmoves['keep']} move={fmoves['move']} "
              f"quarentena={fmoves['quarantine']} · keys {n_before}→{n_after} · "
              f"n_obs {obs_before}={obs_after} ✓ · n_moves {mv_before}={mv_after} ✓")

    # ------------------------------------------------------------- ticks
    ticks_moved = ticks_line_amb = 0
    impure_gids = {f"sofa:{sid}" for sid in impure if not target_sid or sid == str(target_sid)}
    for tf in sorted(ticks_dir.glob("*.jsonl")):
        rows, changed = [], False
        for ln in tf.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except Exception:
                rows.append(ln)
                continue
            gid = str(row.get("gid") or "")
            if gid in impure_gids:
                if row.get("kind") == "line_move":
                    sid = gid.replace("sofa:", "", 1)
                    dests = assign_by_sid.get(sid, {}).get((row.get("casa"), row.get("mercado")))
                    if dests and len(dests) == 1:
                        row = _retarget_tick(row, next(iter(dests)))
                        changed = True
                        ticks_moved += 1
                    elif dests:
                        ticks_line_amb += 1
                else:
                    a = assign.get((row.get("casa"), row.get("mercado"),
                                    line_key(row.get("linha")), row.get("lado")))
                    dest = (a or {}).get(gid)
                    if dest:
                        row = _retarget_tick(row, dest)
                        changed = True
                        ticks_moved += 1
            rows.append(row)
        if changed and not dry:
            out = "".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n"
                          for r in rows)
            atomic_write_text(tf, out)

    report["totals"] = {
        "keys_kept": tot_keep, "keys_moved": tot_move, "keys_quarantined": tot_quar,
        "obs_reatribuidas": tot_obs, "moves_reatribuidos": tot_moves,
        "settlements_resetados": tot_resets,
        "ticks_moved": ticks_moved, "ticks_line_move_ambiguos": ticks_line_amb,
        "changes": changes,
    }
    print(f"[split] TOTAL: keep={tot_keep} · move={tot_move} · quarentena={tot_quar} · "
          f"n_obs reatribuídas={tot_obs} · n_moves reatribuídos={tot_moves} · "
          f"settles resetados={tot_resets} · "
          f"ticks regravados={ticks_moved} (line_move ambíguos: {ticks_line_amb})"
          + (" · DRY-RUN (nada gravado)" if dry else ""))
    _write_report(rep_dir, report, dry)
    return report


def _retarget_tick(row, dest_gid):
    row = dict(row)
    row["gid"] = dest_gid
    if dest_gid.startswith("sofa:"):
        row["sofa_id"] = dest_gid.split(":", 1)[1]
    else:
        row.pop("sofa_id", None)
        parts = dest_gid.split("|")
        if len(parts) == 3:
            row["djogo"], row["home"], row["away"] = parts
    return row


def _owner_from_pairs(all_keys, sid):
    """Sem evidência externa: dono = identidade das chaves com match 'pair'."""
    from collections import Counter
    c = Counter()
    for k, v in all_keys.items():
        meta = parse_history_key(k)
        if str(v.get("sofa_id") or meta.get("sofa_id") or "") != sid:
            continue
        if (v.get("match_method") or "") == "pair":
            hr = norm_team(v.get("home_raw") or "")
            ar = norm_team(v.get("away_raw") or "")
            if hr and ar:
                c[(hr, ar)] += 1
    if not c:
        return None
    (hn, an), _n = c.most_common(1)[0]
    return {"hn": hn, "an": an}


def _write_report(rep_dir, report, dry):
    rep_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(BRT).strftime("%Y%m%d_%H%M%S")
    suffix = "_dry" if dry else ""
    path = rep_dir / f"sporting_split_{stamp}{suffix}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[split] relatório → {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(HERE), help="raiz dos dados (default: repo)")
    ap.add_argument("--dry", action="store_true", help="só relata, não grava")
    ap.add_argument("--sofa-id", default=None, help="restringe a um sofa_id (default: o da evidência)")
    ap.add_argument("--all", action="store_true", dest="all_impure",
                    help="trata TODOS os sofa_ids impuros (sem evidência → quarentena)")
    ap.add_argument("--evidence", default=None, help="path relativo do arquivo de evidência")
    args = ap.parse_args(argv)
    migrate_root(Path(args.root), args.dry, target_sid=args.sofa_id,
                 evidence_path=args.evidence, all_impure=args.all_impure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
