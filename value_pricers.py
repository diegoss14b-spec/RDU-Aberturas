# -*- coding: utf-8 -*-
"""
value_pricers.py — reprecifica Cartões / Faltas / Finalizações em Python, batendo 1:1
com os precificadores do navegador. Lê as CONSTANTES VIVAS das páginas (fica em sincronia
se recalibrar) e os DADOS DOS TIMES das mesmas fontes que as páginas usam.

Cada pricer.price(...) -> dict canônico ou None (sem cobertura):
  {mu, p_over_win, p_under_win, p_push, p_over, p_under}
  p_over/p_under são aliases de win-only (NÃO 1-p_over quando há push).

Linha inteira L: push = P(X=L); over_win = P(X>L); under_win = P(X<L).
Meia linha: p_push=0; over = 1-CDF(floor(L)); under = CDF(floor(L)).

Convenção de liga por modelo (o resolver de evento cuida do mapeamento):
  Cartões:      A, B, PL, BU, LL, SA, L1
  Faltas:       BR-A, BR-B, PL, BU, LL, SA, L1
  Finalizações: BR, PL, L1, LL, BU, SA (+ AU, AR, MX)
"""
import json, math, re
from pathlib import Path
from collections import defaultdict, deque
from pricing_math import ou_probs_from_cdf, price_dict

ROOT = Path(__file__).resolve().parent
DEPLOY = ROOT / "netlify-deploy"
# modo NUVEM: se existir o bundle, os pricers carregam dele (sem HTML nem matches.json)
BUNDLE_PATH = ROOT / "data" / "pricer_data.json"
_BUNDLE = json.loads(BUNDLE_PATH.read_text(encoding="utf-8")) if BUNDLE_PATH.exists() else None


def pois_cdf(k, mu):
    if k < 0: return 0.0
    s = 0.0; t = math.exp(-mu)
    for i in range(0, k + 1):
        if i > 0: t *= mu / i
        s += t
    return min(1.0, s)


def nb_cdf(k, mu, phi):
    if k < 0: return 0.0
    if phi <= 1.001 or mu <= 0:
        return pois_cdf(k, mu)
    r = mu / (phi - 1.0); p = r / (r + mu); s = 0.0; pmf = p ** r
    for i in range(0, k + 1):
        if i > 0: pmf *= (r + i - 1) / i * (1 - p)
        s += pmf
    return min(1.0, s)


def _read(path):
    return (DEPLOY / path).read_text(encoding="utf-8", errors="replace")


def _tk(s):  # "lg|id" -> (lg, int(id))
    lg, tid = s.rsplit("|", 1)
    return (lg, int(tid))


# ---------------- CARTÕES (Poisson) ----------------
class CardsPricer:
    market = "cartoes"

    def __init__(self):
        if _BUNDLE:
            b = _BUNDLE["cards"]
            self.ybar, self.beta = b["ybar"], b["beta"]
            self.by = {_tk(k): v for k, v in b["teams"].items()}
            self.leagues = set(lg for lg, _ in self.by)
            return
        h = _read("Modelo Preditivo de Cartoes v2.html")
        # CALm dos amarelos (sem vermelhos): {y:.., b:..}
        m = re.search(r"const CALm = withReds \? \{[^}]*\} : \{y:([\d.]+), b:([\d.]+)\}", h)
        self.ybar, self.beta = float(m.group(1)), float(m.group(2))
        m = re.search(r"const TEAMS=", h)
        teams, _ = json.JSONDecoder().raw_decode(h, m.end())
        self.by = {}
        self.leagues = set(teams.keys())
        for lg, arr in teams.items():
            for t in arr:
                self.by[(lg, int(t["id"]))] = t

    def price(self, lg, home_id, away_id, line):
        h = self.by.get((lg, int(home_id))); a = self.by.get((lg, int(away_id)))
        if not h or not a: return None
        raw = (h["h_y_iss"] + a["a_y_rec"]) / 2 + (a["a_y_iss"] + h["h_y_rec"]) / 2
        mu = max(0.5, self.ybar + self.beta * (raw - self.ybar))
        po, pu, pp = ou_probs_from_cdf(pois_cdf, mu, line)
        return price_dict(mu, po, pu, pp)


# ---------------- FINALIZAÇÕES / TOTAL DE CHUTES (Binomial Negativa) ----------------
class ShotsPricer:
    market = "finalizacoes"

    def __init__(self):
        if _BUNDLE:
            b = _BUNDLE["shots"]
            self.beta, self.phi, self.lgavg = b["beta"], b["phi"], b["lgavg"]
            self.by = {_tk(k): v for k, v in b["teams"].items()}
            self.leagues = set(lg for lg, _ in self.by)
            return
        h = _read("Modelo Preditivo de Finalizacoes v3.html")
        m = re.search(r"lgTot \+ ([\d.]+)\*\(vcMu - lgTot\)", h)
        self.beta = float(m.group(1))
        m = re.search(r"window\._distPhi=([\d.]+), calibDist", h)
        self.phi = float(m.group(1))
        m = re.search(r"const LG_AVGS=", h)
        self.lgavg, _ = json.JSONDecoder().raw_decode(h, m.end())
        mt = re.search(r"const T_DATA=`([^`]*)`", h)
        self.by = {}
        self.leagues = set()
        for ln in mt.group(1).strip().split("\n"):
            if not ln: continue
            p = ln.split("\t")
            self.leagues.add(p[0])
            self.by[(p[0], int(p[1]))] = {"fH": float(p[3]), "fA": float(p[4]),
                                          "aH": float(p[5]), "aA": float(p[6])}

    def price(self, lg, home_id, away_id, line):
        h = self.by.get((lg, int(home_id))); a = self.by.get((lg, int(away_id)))
        if not h or not a or lg not in self.lgavg: return None
        vcmu = (h["fH"] + a["aA"]) / 2 + (a["fA"] + h["aH"]) / 2
        lgtot = self.lgavg[lg]["lgH"] + self.lgavg[lg]["lgA"]
        mu = max(1.0, lgtot + self.beta * (vcmu - lgtot))
        po, pu, pp = ou_probs_from_cdf(nb_cdf, mu, line, self.phi)
        return price_dict(mu, po, pu, pp)


# ---------------- ESCANTEIOS (Ridge por lado + blend liga, Poisson) ----------------
class CornersPricer:
    market = "escanteios"

    def __init__(self):
        if _BUNDLE and _BUNDLE.get("corners"):
            b = _BUNDLE["corners"]
            self.C = b["C"]; self.lgavg = b["lgavg"]
            self.by = {_tk(k): v for k, v in b["teams"].items()}
            self.leagues = set(lg for lg, _ in self.by)
            return
        h = _read("Modelo Preditivo de Escanteios v2.html")
        m = re.search(r"const C=", h)
        self.C, _ = json.JSONDecoder().raw_decode(h, m.end())
        m = re.search(r"const LG_AVGS=", h)
        self.lgavg, _ = json.JSONDecoder().raw_decode(h, m.end())
        mt = re.search(r"const T_DATA=`([^`]*)`", h)
        self.by = {}; self.leagues = set()
        for ln in mt.group(1).strip().split("\n"):
            if not ln: continue
            p = ln.split("\t")
            self.leagues.add(p[0])
            self.by[(p[0], int(p[1]))] = {
                "fH": float(p[3]), "fA": float(p[4]), "cH": float(p[5]), "cA": float(p[6]),
                "recF": float(p[7]) if len(p) > 7 and p[7] else None,
                "recA": float(p[8]) if len(p) > 8 and p[8] else None}

    def _side(self, side, own, opp, lg):
        w = self.C[side]
        if side == "home":
            f = [own["fH"], opp["cA"], own["recF"] if own["recF"] is not None else own["fH"],
                 opp["recA"] if opp["recA"] is not None else opp["cA"], lg / 2]
        else:
            f = [own["fA"], opp["cH"], own["recF"] if own["recF"] is not None else own["fA"],
                 opp["recA"] if opp["recA"] is not None else opp["cH"], lg / 2]
        return w[0] + w[1] * f[0] + w[2] * f[1] + w[3] * f[2] + w[4] * f[3] + w[5] * f[4]

    def price(self, lg, home_id, away_id, line):
        h = self.by.get((lg, int(home_id))); a = self.by.get((lg, int(away_id)))
        if not h or not a or lg not in self.lgavg: return None
        lgtot = self.lgavg[lg]["tot"]
        ph = self._side("home", h, a, lgtot); pa = self._side("away", a, h, lgtot)
        bl = self.C["blend"]
        mu = max(1.0, (1 - bl) * (ph + pa) + bl * lgtot)
        po, pu, pp = ou_probs_from_cdf(pois_cdf, mu, line)
        return price_dict(mu, po, pu, pp)


# ---------------- FALTAS (Binomial Negativa, μ recência de matches.json) ----------------
class FoulsPricer:
    market = "faltas"
    FHL = 8  # meia-vida da recência fc/fs (igual à validação walk-forward)
    LIGAS = {"PL", "BU", "LL", "SA", "L1", "BR-A", "BR-B"}

    def __init__(self):
        if _BUNDLE:
            b = _BUNDLE["fouls"]
            self.beta, self.phi = b["beta"], b["phi"]
            self.fc = {int(k): v for k, v in b["fc"].items() if k.lstrip("-").isdigit()}
            self.fs = {int(k): v for k, v in b["fs"].items() if k.lstrip("-").isdigit()}
            self.lgtot = dict(b["lgtot"])
            return
        c = json.loads((ROOT / "data/calibration/fouls.json").read_text(encoding="utf-8"))
        self.beta, self.phi = c["beta"], c["phi"]

        def g(m, k, s): return (m.get("stats") or {}).get(k, {}).get(s)
        M = [m for m in json.loads((ROOT / "data/unified/matches.json").read_text(encoding="utf-8"))["matches"]
             if m["competition"] in self.LIGAS and m.get("date")
             and g(m, "fouls", "h") is not None and g(m, "fouls", "a") is not None]
        M.sort(key=lambda m: (m["date"], m.get("match_id") or 0))
        fcH = defaultdict(lambda: deque(maxlen=80))   # por TIME (igual à validação)
        fsH = defaultdict(lambda: deque(maxlen=80))
        lgT = defaultdict(lambda: deque(maxlen=300))  # por LIGA
        for m in M:
            comp = m["competition"]; h = m["home"]["id"]; a = m["away"]["id"]
            fh, fa = g(m, "fouls", "h"), g(m, "fouls", "a")
            fcH[h].append(fh); fsH[h].append(fa); fcH[a].append(fa); fsH[a].append(fh)
            lgT[comp].append(fh + fa)

        def wavg(dq, hl):
            if not dq: return None
            lam = math.log(2) / hl; ws = vs = 0.0
            for i, x in enumerate(reversed(dq)):
                w = math.exp(-lam * i); vs += w * x; ws += w
            return vs / ws
        self.fc = {t: wavg(dq, self.FHL) for t, dq in fcH.items() if len(dq) >= 3}
        self.fs = {t: wavg(dq, self.FHL) for t, dq in fsH.items() if len(dq) >= 3}
        self.lgtot = {c: wavg(dq, 120) for c, dq in lgT.items() if len(dq) >= 40}

    def price(self, comp, home_id, away_id, line):
        fch = self.fc.get(home_id); fsh = self.fs.get(home_id)
        fca = self.fc.get(away_id); fsa = self.fs.get(away_id)
        lg = self.lgtot.get(comp)
        if None in (fch, fsh, fca, fsa) or lg is None: return None
        mu = (fch + fsa) / 2 + (fca + fsh) / 2
        mup = lg + self.beta * (mu - lg)
        po, pu, pp = ou_probs_from_cdf(nb_cdf, mup, line, self.phi)
        return price_dict(mup, po, pu, pp)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cp, sp, fp, xp = CardsPricer(), ShotsPricer(), FoulsPricer(), CornersPricer()
    print(f"Cartões: ȳ={cp.ybar} β={cp.beta} · ligas={sorted(cp.leagues)} · {len(cp.by)} times")
    print(f"Finaliz: β={sp.beta} φ={sp.phi} · ligas={sorted(sp.leagues)} · {len(sp.by)} times")
    print(f"Faltas:  β={fp.beta} φ={fp.phi} · times c/ fc={len(fp.fc)} · ligas c/ base={sorted(fp.lgtot)}")
    print(f"Escant.: blend={xp.C['blend']} · ligas={sorted(xp.leagues)} · {len(xp.by)} times")
    xpl = [tid for (lg, tid) in xp.by if lg == "PL"][:2]
    if len(xpl) == 2:
        h, a = xpl
        for L in (8.5, 9.5, 10.5):
            r = xp.price("PL", h, a, L)
            print(f"  ESC PL {h}x{a} L{L}: μ={r['mu']:.2f} over={r['p_over']*100:.1f}% (justa {1/max(r['p_over'],1e-6):.2f})")
    # self-test: Arsenal(42) x Man City(17)? procurar 2 ids da PL nos cartões
    pl_ids = [tid for (lg, tid) in cp.by if lg == "PL"][:2]
    if len(pl_ids) == 2:
        h, a = pl_ids
        for L in (3.5, 4.5, 5.5, 6.5):
            r = cp.price("PL", h, a, L)
            print(f"  CART PL {h}x{a} L{L}: μ={r['mu']:.2f} over={r['p_over']*100:.1f}% (justa {1/max(r['p_over'],1e-6):.2f})")
