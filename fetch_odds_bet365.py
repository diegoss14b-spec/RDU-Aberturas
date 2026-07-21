# -*- coding: utf-8 -*-
"""fetch_odds_bet365.py — captura odds da BET365 via BetsAPI (api.b365api.com), pra
a Mesa de Aberturas. Plano do Diego: 30 req/s, 3.600 req/h — cada captura usa
~15 páginas de upcoming + ≤MAX_EVENTS prematch (~75 req, folga enorme).

  lista : GET /v1/bet365/upcoming?sport_id=1&token=&page=   (50/página, ~700 eventos,
          horizonte de meses; POLUÍDO de Esoccer/SRL — filtrar por nome de liga)
  detalhe: GET /v3/bet365/prematch?token=&FI=               (seções cards_fouls/corners/
          asian_lines/other/shots; sp.{mercado}.odds[] = {header:'Over'|'Under'|'1'|'2',
          name:'5.5'|'Over 5.5', odds:'2.000', handicap?})

Mercados capturados (só O/U de linha; faixas/race/exatos/3-vias ficam FORA):
  Cartões    : number_of_cards_in_match + asian_total_cards (+ team_cards por time)
  Escanteios : corners_2_way + alternative_corners + asian_corners + asian_total_corners
               (+ team_corners por time)  — corners.corners é 3-VIAS (Over/Exactly/Under), NÃO entra
  Finalizações / Chutes no gol: match_shots / match_shots_on_target (+ team_*)
  Faltas de JOGO: a bet365 não oferece (só player props) — casa entra sem faltas.
⚠ Mercados enchem ao longo do dia do jogo (team_cards/match_shots vazios de madrugada):
  gravamos o que houver a cada captura; o modelo abertura→close da Mesa lida com isso.

SEGREDO: token via env BETSAPI_TOKEN (GitHub Actions secret — o repo é público, o
token JAMAIS vai em código/commit) com fallback betsapi_config.json local (gitignored).
Saída: data/odds/bet365_{stamp}.jsonl + bet365_latest.json (formato normalizado do board)."""
import sys, os, json, re, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from capture_common import odds_window, in_window
except Exception:
    def odds_window(): return None
    def in_window(_s, _w): return True
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
BASE = "https://api.b365api.com"
DAYS_AHEAD = 5           # janela da Mesa
MAX_EVENTS = 60          # cap de chamadas de prematch por captura (orçamento ~75 req no total)
MAX_PAGES = 20           # upcoming pagina de 50 em 50 (~700 eventos = 14 páginas)
SLEEP = 0.12             # ≤10 req/s — bem abaixo do limite de 30 req/s do plano
MIN_EVENTS = 5
MIN_EFF = MIN_EVENTS     # modo close (ODDS_WINDOW_H) reduz — ver main()

# ligas falsas (bots/simulação) — NUNCA entram
EXCL_LEAGUE = re.compile(r"esoccer|e-?soccer|srl\b|\(srl\)|virtual|simulat", re.I)
# prioridade 0 = ligas com MODELO na Mesa (cartões/faltas/finalizações/escanteios)
PRIO_LEAGUE = re.compile(
    r"brazil serie [ab]|premier league|la liga|italy serie a|bundesliga|ligue 1|"
    r"eliteserien|bolivia|ecuador|china super league", re.I)
# prioridade 1 = competições que a Mesa acompanha de perto
SEC_LEAGUE = re.compile(
    r"brazil|libertadores|sudamericana|sul-americana|argentina|mexico|colombia|"
    r"uefa|champions|europa|conference|championship|eredivisie|primeira liga|mls", re.I)


def _token():
    tok = (os.environ.get("BETSAPI_TOKEN") or "").strip()
    if tok:
        return tok
    for p in (ROOT / "betsapi_config.json", ROOT.parent / "betsapi_config.json"):
        try:
            if p.exists():
                tok = (json.loads(p.read_text(encoding="utf-8")).get("token") or "").strip()
                if tok:
                    return tok
        except Exception:
            pass
    raise RuntimeError("BETSAPI_TOKEN ausente (env ou betsapi_config.json)")


def get(path, params, token):
    """GET com retry; loga SEM o token (o token nunca pode vazar em log público)."""
    url = f"{BASE}{path}"
    q = dict(params); q["token"] = token
    for a in range(3):
        try:
            r = requests.get(url, params=q, timeout=30)
            if r.status_code == 200:
                d = r.json()
                if d.get("success") == 1:
                    return d
                # success:0 = token/quota — não insistir além do retry
                print(f"[bet365] {path} success=0 (tentativa {a+1}): {str(d)[:120]}")
            elif r.status_code == 429:
                time.sleep(2.0 * (a + 1)); continue
            else:
                print(f"[bet365] {path} HTTP {r.status_code} (tentativa {a+1})")
        except Exception as e:
            print(f"[bet365] {path} erro: {type(e).__name__} (tentativa {a+1})")
        time.sleep(1.0)
    return None


# --- parse das odds -----------------------------------------------------------
_OU_NAME = re.compile(r"^(over|under)\s+([0-9.]+)$", re.I)


def _num(s):
    try:
        v = float(str(s).strip())
        return v
    except Exception:
        return None


def _entry_side_line(o):
    """Uma odd da BetsAPI → (side, linha) ou None.
    Formatos: header Over/Under + name/handicap numérico  |  name/handicap 'Over 5.5'."""
    header = str(o.get("header") or "").strip().lower()
    name = str(o.get("name") or "").strip()
    hcap = str(o.get("handicap") or "").strip()
    if "," in name or "," in hcap:
        return None  # linha asiática quartada (5.5,6.0) — não representável no par O/U
    if header in ("over", "under"):
        L = _num(name) if _num(name) is not None else _num(hcap)
        if L is None:
            return None
        return header, L
    for txt in (name, hcap):
        mo = _OU_NAME.match(txt)
        if mo:
            return mo.group(1).lower(), float(mo.group(2))
    return None


def _collect(lines, odds_list):
    """Acumula pares O/U em lines[L] = {'over','under'} (primeiro valor vence)."""
    for o in odds_list or []:
        sl = _entry_side_line(o)
        price = _num(o.get("odds"))
        if not sl or not price or price <= 1:
            continue
        side, L = sl
        slot = lines.setdefault(L, {})
        slot.setdefault(side, round(price, 2))


def _collect_team(per_team, odds_list, home, away):
    """Mercados por time: header '1'(casa)/'2'(fora) + handicap 'Over 5.5'."""
    for o in odds_list or []:
        header = str(o.get("header") or "").strip()
        team = home if header == "1" else (away if header == "2" else None)
        if not team:
            continue
        sl = None
        for txt in (str(o.get("handicap") or ""), str(o.get("name") or "")):
            mo = _OU_NAME.match(txt.strip())
            if mo:
                sl = (mo.group(1).lower(), float(mo.group(2)))
                break
        price = _num(o.get("odds"))
        if not sl or not price or price <= 1:
            continue
        side, L = sl
        slot = per_team.setdefault(team, {}).setdefault(L, {})
        slot.setdefault(side, round(price, 2))


# (seção, mercado) → canon da Mesa. Ordem = prioridade (linha principal primeiro).
MATCH_MARKETS = [
    ("cards_fouls", "number_of_cards_in_match", "Cartões"),
    ("asian_lines", "asian_total_cards", "Cartões"),
    ("corners", "corners_2_way", "Escanteios"),
    ("corners", "alternative_corners", "Escanteios"),
    ("corners", "asian_corners", "Escanteios"),
    ("asian_lines", "asian_total_corners", "Escanteios"),
    ("other", "match_shots", "Finalizações"),
    ("shots", "match_shots", "Finalizações"),
    ("other", "match_shots_on_target", "Chutes no gol"),
    ("shots", "match_shots_on_target", "Chutes no gol"),
]
TEAM_MARKETS = [
    ("cards_fouls", "team_cards", "Cartões"),
    ("corners", "team_corners", "Escanteios"),
    ("other", "team_shots", "Finalizações"),
    ("shots", "team_shots", "Finalizações"),
    ("other", "team_shots_on_target", "Chutes no gol"),
    ("shots", "team_shots_on_target", "Chutes no gol"),
]


def parse_prematch(res, home, away):
    """results[0] do /v3/bet365/prematch → (mercados, mercados_time)."""
    merc, merc_t = {}, {}
    for sec, mk, canon in MATCH_MARKETS:
        sp = ((res.get(sec) or {}).get("sp") or {})
        mv = sp.get(mk) or {}
        odds = mv.get("odds") or []
        if not odds:
            continue
        lines = merc.setdefault(canon, {})
        _collect(lines, odds)
    out = {}
    for canon, lines in merc.items():
        arr = [{"linha": L, "over": v["over"], "under": v["under"]}
               for L, v in sorted(lines.items()) if "over" in v and "under" in v]
        if arr:
            out[canon] = arr
    for sec, mk, canon in TEAM_MARKETS:
        sp = ((res.get(sec) or {}).get("sp") or {})
        mv = sp.get(mk) or {}
        odds = mv.get("odds") or []
        if not odds:
            continue
        per_team = {}
        _collect_team(per_team, odds, home, away)
        for team, lines in per_team.items():
            arr = [{"linha": L, "over": v["over"], "under": v["under"]}
                   for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if arr:
                merc_t.setdefault(canon, {})[team] = arr
    return out, merc_t


def main():
    token = _token()
    now = datetime.now(BRT)
    now_utc = datetime.now(timezone.utc)

    # 1) upcoming paginado (barato: ~14 req) → filtra liga real + janela de dias
    events, total = [], None
    page = 1
    while page <= MAX_PAGES:
        d = get("/v1/bet365/upcoming", {"sport_id": 1, "page": page}, token)
        if not d:
            break
        total = (d.get("pager") or {}).get("total") or 0
        for r in d.get("results") or []:
            league = ((r.get("league") or {}).get("name")) or ""
            if EXCL_LEAGUE.search(league):
                continue
            try:
                t = int(r.get("time") or 0)
            except Exception:
                continue
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            if not (now_utc - timedelta(hours=3) <= dt <= now_utc + timedelta(days=DAYS_AHEAD)):
                continue
            home = ((r.get("home") or {}).get("name")) or ""
            away = ((r.get("away") or {}).get("name")) or ""
            if not home or not away:
                continue
            events.append({"fi": r.get("id"), "time": t, "league": league,
                           "home": home, "away": away})
        if page * 50 >= (total or 0):
            break
        page += 1
        time.sleep(SLEEP)
    print(f"[bet365] upcoming: {total} eventos brutos · {len(events)} reais na janela de {DAYS_AHEAD}d ({page} páginas)")

    _wh = odds_window()
    if _wh is not None:  # modo close: só jogos iminentes
        global MIN_EFF
        _tot = len(events)
        events = [e for e in events if in_window(e["time"], _wh)]
        MIN_EFF = (min(MIN_EVENTS, 1) if events else 0)
        print(f"[bet365] modo close: janela {_wh:g}h -> {len(events)} de {_tot} eventos")

    # prioridade: ligas com modelo > competições acompanhadas > resto; depois kickoff
    def prio(e):
        lg = e["league"]
        p = 0 if PRIO_LEAGUE.search(lg) else (1 if SEC_LEAGUE.search(lg) else 2)
        return (p, e["time"])
    events.sort(key=prio)
    events = events[:MAX_EVENTS]

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"bet365_{stamp}.jsonl"
    from capture_common import write_odds_latest
    def write_latest(n, promote=None):
        write_odds_latest("bet365", out_path.name, n,
                          at=now.isoformat(timespec="seconds"), promote_full=promote,
                          min_events=MIN_EFF)

    # 2) prematch por FI (sequencial + sleep: gentil com o rate-limit)
    f = open(out_path, "w", encoding="utf-8")
    n_out = n_det = 0
    for e in events:
        d = get("/v3/bet365/prematch", {"FI": e["fi"]}, token)
        time.sleep(SLEEP)
        if not d:
            continue
        rs = d.get("results") or []
        if not rs:
            continue
        n_det += 1
        merc, merc_t = parse_prematch(rs[0], e["home"], e["away"])
        if not merc and not merc_t:
            continue
        rec = {"casa": "bet365", "event_id": e["fi"],
               "name": f"{e['home']} - {e['away']}",
               "league": e["league"], "start": e["time"],
               "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
               "mercados": merc}
        if merc_t:
            rec["mercados_time"] = merc_t
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
    f.close()
    write_latest(n_out, promote=None)
    print(f"[bet365] {n_det} prematch consultados · {n_out} jogos com mercado de estatística salvos em {out_path.name}")
    return n_out


if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    from capture_common import finish
    try:
        _n = main() or 0
        sys.exit(finish("bet365", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("bet365", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
