# -*- coding: utf-8 -*-
"""history_ingest.py — BANCO DE ODDS. A cada captura, lê {casa}_latest e registra:
  - TICK (append ticks/{YYYY-MM-DD}.jsonl) quando odd mudou ≥0.01 ou 1ª obs
  - KEY (upsert keys/{YYYY-MM}.json) open/last/min/max/n_obs/n_moves + sofa_id

Chave canônica (13/07):
  com Sofa:  casa|sofa:{id}|mercado|linha|lado
  sem Sofa:  casa|data|home_norm|away_norm|mercado|linha|lado
"""
import json, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import resolve_fixture, history_key, load_sofa_fixtures, parse_start, norm_team

ODDS = ROOT / "data" / "odds"
HIST = ROOT / "data" / "odds_history"
BRT = timezone(timedelta(hours=-3))
CASAS = ["betano", "superbet", "estrelabet", "7k", "pinnacle"]

BETANO_MK = {
    "Total de Cartões": "Cartões", "Total de Faltas": "Faltas", "Total de chutes": "Finalizações",
    "Total de Impedimentos": "Impedimentos", "Total de laterais": "Laterais",
    "Total de tiros de meta": "Tiros de meta", "Escanteios": "Escanteios",
    "Chutes no gol": "Chutes no gol",
}


def load_events(casa):
    ptr = ODDS / f"{casa}_latest.json"
    if not ptr.exists():
        return []
    try:
        fn = json.loads(ptr.read_text(encoding="utf-8")).get("file")
        src = ODDS / fn if fn else None
        if not src or not src.exists():
            return []
        evs = []
        for ln in src.read_text(encoding="utf-8").strip().split("\n"):
            if not ln.strip():
                continue
            e = json.loads(ln)
            if casa == "betano":
                mk = {}
                for aba in ("cartoes", "estatisticas", "principais_ou", "escanteios"):
                    for m in (e.get("markets", {}).get(aba) or []):
                        canon = BETANO_MK.get(m.get("market"))
                        if canon and m.get("over") and m.get("under") and m.get("line") is not None:
                            mk.setdefault(canon, {})[m["line"]] = {
                                "linha": m["line"], "over": m["over"], "under": m["under"]
                            }
                merc = {c: list(v.values()) for c, v in mk.items()}
            else:
                merc = e.get("mercados") or {}
            if not merc:
                continue
            name = e.get("name") or ""
            parts = [p.strip() for p in name.replace(" vs. ", " - ").replace(" vs ", " - ").split(" - ")]
            home_raw = parts[0] if parts else name
            away_raw = parts[1] if len(parts) > 1 else ""
            evs.append({
                "name": name, "start": e.get("start"), "league": e.get("league") or "",
                "mercados": merc, "home_raw": home_raw, "away_raw": away_raw,
            })
        return evs
    except Exception as ex:
        print(f"[ingest] {casa}: erro ({type(ex).__name__}: {ex})")
        return []


def main():
    now = datetime.now(BRT)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    (HIST / "ticks").mkdir(parents=True, exist_ok=True)
    (HIST / "keys").mkdir(parents=True, exist_ok=True)
    month = now.strftime("%Y-%m")
    kf = HIST / "keys" / f"{month}.json"
    keys = json.loads(kf.read_text(encoding="utf-8")) if kf.exists() else {}
    fixtures = load_sofa_fixtures()
    tick_path = HIST / "ticks" / f"{now.strftime('%Y-%m-%d')}.jsonl"
    tick_f = tick_path.open("a", encoding="utf-8")

    n_ticks = n_new = n_obs = n_sofa = 0
    for casa in CASAS:
        for ev in load_events(casa):
            idt = resolve_fixture(
                ev["home_raw"], ev["away_raw"], ev["start"],
                league=ev.get("league") or "", fixtures=fixtures,
            )
            if not idt["day"] or idt["day"] == "?":
                continue
            if idt.get("sofa_id"):
                n_sofa += 1
            djogo = idt["day"]
            h, a = idt["hn"], idt["an"]
            for mercado, linhas in (ev["mercados"] or {}).items():
                for l in linhas:
                    linha = l.get("linha")
                    if linha is None:
                        continue
                    for lado, odd in (("over", l.get("over")), ("under", l.get("under"))):
                        if not odd or odd <= 1.01 or odd > 50:
                            continue
                        n_obs += 1
                        key = history_key(
                            casa, djogo, h, a, mercado, linha, lado,
                            sofa_id=idt.get("sofa_id"),
                        )
                        k = keys.get(key)
                        changed = (k is None) or (abs((k.get("last_odd") or 0) - odd) >= 0.01)
                        if k is None:
                            keys[key] = k = {
                                "open_odd": odd, "open_ts": now_iso, "open_is_first_seen": True,
                                "close_odd": None, "close_ts": None,
                                "last_odd": odd, "last_ts": now_iso,
                                "n_obs": 0, "n_moves": 0,
                                "max_odd": odd, "min_odd": odd,
                                "kickoff": idt.get("kickoff_iso") or "",
                                "home_raw": ev["home_raw"], "away_raw": ev["away_raw"],
                                "home_norm": h, "away_norm": a,
                                "sofa_id": idt.get("sofa_id"),
                                "match_method": idt.get("match_method"),
                                "match_confidence": idt.get("match_confidence"),
                                "result": None, "won": None, "clv_pct": None, "status": "open",
                            }
                            n_new += 1
                        else:
                            if changed and k.get("status") == "open":
                                k["n_moves"] = (k.get("n_moves") or 0) + 1
                            # atualiza metadados canônicos se ainda faltava sofa
                            if idt.get("sofa_id") and not k.get("sofa_id"):
                                k["sofa_id"] = idt["sofa_id"]
                                k["match_method"] = idt.get("match_method")
                                k["match_confidence"] = idt.get("match_confidence")
                        k["n_obs"] = (k.get("n_obs") or 0) + 1
                        if k.get("status") == "open":
                            k["last_odd"] = odd
                            k["last_ts"] = now_iso
                            k["max_odd"] = max(k.get("max_odd") or odd, odd)
                            k["min_odd"] = min(k.get("min_odd") or odd, odd)
                        if changed:
                            tick_f.write(json.dumps({
                                "ts": now_iso, "casa": casa, "kickoff": k.get("kickoff"),
                                "home": h, "away": a, "mercado": mercado,
                                "linha": linha, "lado": lado, "odd": odd,
                                "sofa_id": k.get("sofa_id"),
                                "djogo": djogo,
                            }, ensure_ascii=False) + "\n")
                            n_ticks += 1
    tick_f.close()
    kf.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    print(
        f"[ingest] {n_obs:,} obs · {n_ticks:,} ticks · {n_new:,} keys novas · "
        f"sofa_match={n_sofa} · total keys mês={len(keys):,}"
    )


if __name__ == "__main__":
    main()
