# -*- coding: utf-8 -*-
"""fetch_odds_betfast.py — captura odds da BETFAST (bet.br; sportsbook BetConstruct
"sportsbookv4" em iframe, brand/clientID 99). Feed REST público, SEM login e SEM proxy
(IP de datacenter serve — validado 21/07/2026). ⚠️ os headers Origin/Referer do site
betfast.bet.br + User-Agent de browser são OBRIGATÓRIOS (sem eles → 403).

Endpoints (host analytics-sp.googleserv.tech):
  árvore  : /api/sport/getheader/pt                    → Sports["1"].Regions→Champs→GameSmallItems
  times   : /api/sport/getheader/teams/pt              → [{Sport,ID,Name}] (nomes de t1/t2)
  catálogo: /api/prematch/getprematchmarketsbysport/pt/,1, → pos ids Over/Under por mercado
  jogo    : /api/prematch/getprematchgamefull/99/{gid} (~300KB) → game.ev{marketId:{oddId:{pos,coef,h,lock}}}
O resumido getprematchgameall NÃO traz os especiais (só ~6 mercados de cabeçalho) →
é full por jogo mesmo, restrito à janela do dia (WINDOW_H).

⚠️ PARTICULARIDADE OPERACIONAL (veredito da varredura de 21/07/2026): a Betfast só ABRE
os mercados especiais (cartões/faltas/escanteios/chutes) NO DIA do jogo, de manhã.
O fetcher roda em todo full/close normalmente e, em dia vazio (sem jogo na janela ou
especiais ainda fechados), sai com 0 eventos SEM erro (MIN_EFF=0) — a "abertura" da
Betfast é a abertura do dia, e o modelo abertura→close da Mesa absorve isso.
Guarda anti-regressão: se ≥5 jogos foram baixados e NENHUM trouxe ev algum, é mudança
de formato do feed, não dia vazio → falha explícita.

Saída: data/odds/betfast_{stamp}.jsonl no formato normalizado do board
(mercados/mercados_time, casa "Betfast") + ponteiros via write_odds_latest.
Consumo medido (21/07, dia cheio): árvore 0,7MB + times 0,25MB + catálogo 0,18MB +
até MAX_EVENTS jogos × ~0,3MB ≈ 6-19MB por captura full (close: bem menos, janela curta);
pacing educado (sleep 0,5-0,8s entre jogos)."""
import sys, os, json, re, time, random
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
try:
    import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
except Exception: pass
import requests
from capture_common import odds_window, in_window, finish, write_odds_latest

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
BASE = "https://analytics-sp.googleserv.tech"
BRAND = 99
H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
     "Accept": "application/json",
     "Origin": "https://betfast.bet.br", "Referer": "https://betfast.bet.br/"}
WINDOW_H = 30      # full: só jogos que começam nas próximas 30h (especiais = em geral só
                   # no dia, mas K-League/Eliteserien abrem escanteios na véspera — vale 30h)
MAX_EVENTS = 60    # teto de fulls por rodada (ordenado por kickoff: os próximos primeiro)
MIN_EVENTS = 1
MIN_EFF = MIN_EVENTS   # vira 0 em dia vazio (legítimo) — ver main()

# Mercados de JOGO INTEIRO: marketId BetConstruct → família canônica da Mesa.
# NÃO incluir 535 (booking POINTS 10/25/... — não é contagem de cartões!)
MK_GAME = {
    "536":   "Cartões",         # Total number of bookings
    "1790":  "Faltas",          # Total Fouls
    "531":   "Escanteios",      # Total corners
    "6874":  "Finalizações",    # Total Shots
    "1777":  "Chutes no gol",   # Shots on target total
    "1783":  "Impedimentos",    # Offsides Total
    "1956":  "Laterais",        # Throw-in total
    "6889":  "Tiros de meta",   # Total Goal Kicks
    "19714": "Desarmes",        # Tackles total
}
# Totais POR TIME: marketId → (família, lado). ⚠️ tackles: 19718=casa, 19716=fora.
MK_TEAM = {
    "1765": ("Cartões", "home"), "1766": ("Cartões", "away"),
    "1791": ("Faltas", "home"), "1792": ("Faltas", "away"),
    "1744": ("Escanteios", "home"), "1745": ("Escanteios", "away"),
    "6875": ("Finalizações", "home"), "6876": ("Finalizações", "away"),
    "1778": ("Chutes no gol", "home"), "1779": ("Chutes no gol", "away"),
    "1784": ("Impedimentos", "home"), "1785": ("Impedimentos", "away"),
    "1959": ("Laterais", "home"), "1960": ("Laterais", "away"),
    "6890": ("Tiros de meta", "home"), "6891": ("Tiros de meta", "away"),
    "19718": ("Desarmes", "home"), "19716": ("Desarmes", "away"),
}
# pos ids Over/Under por mercado (do catálogo de 21/07/2026) — fallback se o
# catálogo vivo falhar. O fetch do catálogo em cada rodada sobrepõe isto.
POS_FALLBACK = {
    "536": (128, 129), "1790": (17177, 17178), "531": (286, 287),
    "6874": (83821, 83822), "1777": (17143, 17144), "1783": (17159, 17160),
    "1956": (27852, 27853), "6889": (83854, 83855), "19714": (116378, 116379),
    "1765": (16439, 16440), "1766": (16441, 16442),
    "1791": (17179, 17180), "1792": (17181, 17182),
    "1744": (16138, 16139), "1745": (16140, 16141),
    "6875": (83823, 83824), "6876": (83825, 83826),
    "1778": (17145, 17146), "1779": (17147, 17148),
    "1784": (17161, 17162), "1785": (17163, 17164),
    "1959": (27858, 27859), "1960": (27860, 27861),
    "6890": (83856, 83857), "6891": (83858, 83859),
    "19716": (116382, 116383), "19718": (116386, 116387),
}
# esoccer/simulados não entram (região/champ "SRL"/"Simulated"; times com apelido "(Gael)")
RX_VIRTUAL = re.compile(r"srl|simulated|e-?sport|esoccer|cyber", re.I)
RX_NICK = re.compile(r"\([A-Za-z .]+\)\s*$")


def jload(x):
    """desembrulha JSON duplamente encodado do feed BetConstruct ('\"{...}\"')."""
    for _ in range(3):
        if not isinstance(x, str):
            return x
        x = json.loads(x)
    return x


def get(path, tries=3, timeout=25):
    for a in range(tries):
        try:
            r = requests.get(BASE + path, headers=H, timeout=timeout)
            if r.status_code == 200:
                return jload(r.json())
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(1.5 * (a + 1))
    return None


def catalog_pos():
    """pos ids Over/Under por mercado, do catálogo vivo; cai no fallback embutido."""
    pos = dict(POS_FALLBACK)
    cat = get(f"/api/prematch/getprematchmarketsbysport/pt/,1,")
    try:
        for block in cat or []:
            for mid, m in (block or {}).items():
                if str(mid) not in pos:
                    continue
                over = under = None
                for p in (m.get("pos") or []):
                    kn = (p.get("kn") or "").strip().lower()
                    if kn == "over": over = p.get("id")
                    elif kn == "under": under = p.get("id")
                if over and under:
                    pos[str(mid)] = (over, under)
    except Exception:
        pass
    return pos


def ou_lines(odds, over_pos, under_pos):
    """ev[marketId] → [{linha, over, under}] (só pares completos e destravados)."""
    by_line = {}
    for o in (odds or {}).values():
        if not isinstance(o, dict) or o.get("lock"):
            continue
        coef, h, p = o.get("coef"), o.get("h"), o.get("pos")
        if not coef or coef <= 1 or h is None:
            continue
        side = "over" if p == over_pos else ("under" if p == under_pos else None)
        if not side:
            continue
        by_line.setdefault(float(h), {})[side] = round(float(coef), 2)
    return [{"linha": L, "over": v["over"], "under": v["under"]}
            for L, v in sorted(by_line.items()) if "over" in v and "under" in v]


def discover(now_utc):
    """árvore + times → jogos de futebol REAIS na janela [agora, agora+WINDOW_H]."""
    tree = get("/api/sport/getheader/pt", timeout=40)
    if not tree:
        raise RuntimeError("árvore getheader indisponível")
    teams = get("/api/sport/getheader/teams/pt", timeout=40) or []
    tname = {t.get("ID"): t.get("Name") for t in teams
             if isinstance(t, dict) and t.get("Sport") == 1}
    lang = next(iter(tree.values()), {})
    fb = (lang.get("Sports") or {}).get("1") or {}
    disabled = set()
    try:
        dis = get(f"/api/sport/getdisablegamesbycompany/{BRAND}")
        for x in dis or []:
            disabled.add(int(x if not isinstance(x, dict) else x.get("ID", 0)))
    except Exception:
        pass
    out = []
    for r in (fb.get("Regions") or {}).values():
        rname = r.get("Name") or ""
        for c in (r.get("Champs") or {}).values():
            cname = c.get("Name") or ""
            if RX_VIRTUAL.search(rname + " " + cname):
                continue
            for g in (c.get("GameSmallItems") or {}).values():
                gid = g.get("ID") or 0
                if gid <= 0 or gid in disabled:    # gid negativo = outright
                    continue
                st = g.get("StartTime") or ""      # ISO sem tz = UTC (validado 21/07)
                try:
                    dt = datetime.fromisoformat(st).replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if not (now_utc - timedelta(minutes=10) <= dt <= now_utc + timedelta(hours=WINDOW_H)):
                    continue
                t1 = tname.get(g.get("t1")) or ""
                t2 = tname.get(g.get("t2")) or ""
                if not t1 or not t2:
                    continue
                if RX_NICK.search(t1) and RX_NICK.search(t2):   # esoccer "Time (Apelido)"
                    continue
                out.append({"gid": gid, "t1": t1, "t2": t2,
                            "league": f"{rname} · {cname}" if rname else cname,
                            "start_utc": dt})
    out.sort(key=lambda x: (x["start_utc"], x["gid"]))
    return out


def main():
    global MIN_EFF
    now = datetime.now(BRT)
    now_utc = datetime.now(timezone.utc)
    games = discover(now_utc)
    print(f"[betfast] árvore: {len(games)} jogos reais na janela de {WINDOW_H}h")
    _wh = odds_window()
    if _wh is not None:   # modo close: só jogos iminentes
        tot = len(games)
        games = [g for g in games if in_window(int(g["start_utc"].timestamp()), _wh)]
        print(f"[betfast] modo close: janela {_wh:g}h -> {len(games)} de {tot} jogos")
    if not games:
        MIN_EFF = 0       # dia vazio: fonte viva, nada pra capturar — não é falha
    pos = catalog_pos()

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"betfast_{stamp}.jsonl"
    n_out = n_fetch = n_ev = 0
    with out_path.open("w", encoding="utf-8") as f:
        for g in games[:MAX_EVENTS]:
            d = get(f"/api/prematch/getprematchgamefull/{BRAND}/{g['gid']}")
            n_fetch += 1
            time.sleep(random.uniform(0.5, 0.8))
            if not d:
                continue
            try:
                game = jload(d.get("game")) or {}
                ev = game.get("ev") or {}
                dis_mk = {str(x) for x in (jload(d.get("disableMarkets")) or [])}
            except Exception:
                continue
            if ev:
                n_ev += 1
            merc, merc_t = {}, {}
            for mid, fam in MK_GAME.items():
                if mid in dis_mk:
                    continue
                arr = ou_lines(ev.get(mid), *pos[mid])
                if arr:
                    merc[fam] = arr
            for mid, (fam, side) in MK_TEAM.items():
                if mid in dis_mk:
                    continue
                arr = ou_lines(ev.get(mid), *pos[mid])
                if arr:
                    team = g["t1"] if side == "home" else g["t2"]
                    merc_t.setdefault(fam, {})[team] = arr
            if not merc and not merc_t:
                continue
            stunix = game.get("stunix") or int(g["start_utc"].timestamp())
            rec = {"casa": "Betfast", "event_id": g["gid"],
                   "name": f"{g['t1']} - {g['t2']}", "league": g["league"],
                   "start": int(stunix) * 1000,     # epoch ms (sem ambiguidade de fuso)
                   "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"), "mercados": merc}
            if merc_t:
                rec["mercados_time"] = merc_t
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            n_out += 1
    # guarda anti-regressão de formato: muitos jogos baixados e NENHUM com ev algum
    if n_fetch >= 5 and n_ev == 0:
        raise RuntimeError(f"parse: ev vazio nos {n_fetch} jogos baixados (formato do feed mudou?)")
    if n_out == 0:
        MIN_EFF = 0       # jogos existem mas especiais fechados = padrão Betfast, não falha
    write_odds_latest("betfast", out_path.name, n_out,
                      at=now.isoformat(timespec="seconds"), min_events=MIN_EFF)
    print(f"[betfast] {n_fetch} fulls baixados · {n_out} jogos com especiais salvos em {out_path.name}")
    return n_out


if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    try:
        _n = main() or 0
        sys.exit(finish("betfast", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("betfast", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
