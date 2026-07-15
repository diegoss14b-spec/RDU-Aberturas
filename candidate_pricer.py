# -*- coding: utf-8 -*-
"""candidate_pricer.py — precificadores dos MODELOS NOVOS (candidatos MAE 2026-07-13).

TITULAR do BOARD.valor desde 15/07 (modelos novos promovidos pelo Diego em 14/07;
rollback via FORCE_LEGACY_BOARD=1 no build_board). Mesma interface dos oficiais:
  price(lg, home_id, away_id, line) ->
    {mu, p_over_win, p_under_win, p_push, p_over, p_under} | None

Carrega mu bruto pré-computado (bundle candidate_pricer_data.json, keyed por (comp, sofa_id))
e recalcula a probabilidade de QUALQUER linha via a distribuição calibrada OOF:
  mu_cal = a + b*mu_raw   (calibração linear OOF, congelada)
  CDF = Binomial Negativa size-φ (var = mu + mu²/phi); phi None => Poisson.
  Linha inteira: push mass explícita (pricing_math.ou_probs_from_cdf).

Times fora do bundle (promovidos/rebaixados sem amostra) -> None."""
import json, math
from pathlib import Path
from pricing_math import ou_probs_from_cdf, price_dict

ROOT = Path(__file__).resolve().parent
BUNDLE_PATH = ROOT / "data" / "candidate_pricer_data.json"
_B = json.loads(BUNDLE_PATH.read_text(encoding="utf-8")) if BUNDLE_PATH.exists() else None


def _pois_cdf(k, mu):
    if k < 0:
        return 0.0
    s = 0.0
    t = math.exp(-mu)
    for i in range(0, k + 1):
        if i > 0:
            t *= mu / i
        s += t
    return min(1.0, s)


def _nb_cdf_size(k, mu, phi):
    """CDF da NegBin com 'size'=phi (var = mu + mu²/phi). Igual a scipy nbinom(n=phi,
    p=phi/(phi+mu)) e ao prob_over das páginas. phi None/<=0/enorme => Poisson."""
    if k < 0:
        return 0.0
    if phi is None or phi <= 0 or mu <= 0 or phi > 1e6:
        return _pois_cdf(k, mu)
    p = phi / (phi + mu)
    pmf = p ** phi          # P(X=0)
    s = pmf
    for i in range(1, k + 1):
        pmf *= (phi + i - 1) / i * (1.0 - p)
        s += pmf
    return min(1.0, s)


class _Pricer:
    market = None          # sobrescrito pelas subclasses (nome do mercado no bundle)

    def __init__(self):
        self.ok = bool(_B and _B["markets"].get(self.market))
        if self.ok:
            m = _B["markets"][self.market]
            self.a = m["cal"]["a"]
            self.b = m["cal"]["b"]
            self.phi = m["cal"]["phi"]
            self.xwalk = m["xwalk"]
            self.pairs = m["pairs"]
            self.leagues = set(self.pairs.keys())
        else:
            self.a = self.b = 0.0
            self.phi = None
            self.xwalk = {}
            self.pairs = {}
            self.leagues = set()
        self.by = {}   # compat com o branch PC do build_board (não usado no modo nuvem)

    def price(self, lg, home_id, away_id, line):
        if not self.ok or home_id is None or away_id is None:
            return None
        comp = self.xwalk.get(lg, lg)
        cp = self.pairs.get(comp)
        if not cp:
            return None
        mu_raw = cp.get(f"{int(home_id)}|{int(away_id)}")
        if mu_raw is None:
            return None
        mu_cal = max(0.1, self.a + self.b * float(mu_raw))
        po, pu, pp = ou_probs_from_cdf(_nb_cdf_size, mu_cal, line, self.phi)
        # mu exposto = raw (compat com board/UI); probs usam mu_cal
        return price_dict(float(mu_raw), po, pu, pp)


class CardsPricer(_Pricer):
    market = "cards"


class ShotsPricer(_Pricer):
    market = "shots"


class FoulsPricer(_Pricer):
    market = "fouls"


class CornersPricer(_Pricer):
    market = "corners"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for cls in (CardsPricer, ShotsPricer, FoulsPricer, CornersPricer):
        p = cls()
        comps = sorted(p.leagues)
        n = sum(len(v) for v in p.pairs.values())
        print(f"{cls.__name__}: ok={p.ok} · a={p.a:.3f} b={p.b:.3f} phi={p.phi} · comps={comps} · {n} pares")
        # smoke: 1º par de PL (ou 1º comp)
        comp = "PL" if "PL" in p.pairs else (comps[0] if comps else None)
        if comp:
            k = next(iter(p.pairs[comp]))
            h, a = k.split("|")
            board_lg = {v: kk for kk, v in p.xwalk.items()}.get(comp, comp)
            for L in p.__class__.__mro__ and [3.5, 5.5, 9.5, 21.5, 25.5]:
                r = p.price(board_lg, h, a, L)
                if r:
                    print(f"   {comp} {h}x{a} L{L}: μ={r['mu']:.2f} over={r['p_over']*100:.1f}% "
                          f"(justa {1/max(r['p_over'],1e-6):.2f})")
