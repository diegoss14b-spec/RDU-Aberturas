# -*- coding: utf-8 -*-
"""candidate_pricer.py — precificadores dos MODELOS NOVOS (candidatos MAE 2026-07-13).

TITULAR do BOARD.valor desde 15/07 (modelos novos promovidos pelo Diego em 14/07;
rollback via FORCE_LEGACY_BOARD=1 no build_board). Mesma interface dos oficiais:
  price(lg, home_id, away_id, line) ->
    {mu, mu_cal, mu_raw, p_over_win, p_under_win, p_push, p_over, p_under} | None

``mu`` e ``mu_cal`` são a média calibrada realmente usada na CDF; ``mu_raw`` é apenas diagnóstico.

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
        out = price_dict(mu_cal, po, pu, pp)
        out["mu_raw"] = float(mu_raw)
        out["mu_cal"] = float(mu_cal)
        return out


class CardsPricer(_Pricer):
    """CARTÕES: o modelo prevê AMARELOS; a casa paga CARTÕES.

    μ_cartoes = r_liga · μ_amarelos_cal  +  w_liga · λ_liga · ρ_liga · tempero

    • w = 1 + fração de vermelhos DIRETOS (regra escrita: amarelo 1, vermelho
      direto 2, expulsão por 2 amarelos +1 — a contagem de amarelos da Sofa já
      inclui o 1º amarelo do expulso, provado em 2.029/2.165 jogos). Global 1,62.
    • r = offset do degrau IFAB de 01/07 (amarelos −14,1% a/a e o mercado não
      reprecificou). Trava [0,80;1,05]; re-estimado a cada retreino contra o
      RESÍDUO da versão corrente, então DECAI sozinho e não dupla-conta.
    • tempero: multiplicador do λ pelo perfil do jogo (μ relativo à liga + YOE do
      árbitro), clamp [0,60;1,50]. Na Mesa o YOE é 0 (preça no neutro).

    As duas correções saem JUNTAS — replay pós-IFAB (451 jogos, linhas 3,5-6,5):
    viés de P(over) hoje +3,4pp · só vermelho +8,9 · só offset −5,5 · PACOTE +0,9
    (regra escrita) e +6,3 / +9,6 / −2,6 / +1,3 (casa que paga vermelho=1). O
    pacote vence nos dois mundos; qualquer metade sozinha piora.

    FAIL-CLOSED: sem os blocos `reds`+`regime` no bundle, `ok` fica False e o
    board não preça cartões (é melhor não precificar do que precificar amarelos
    como se fossem cartões).
    """

    market = "cards"

    def __init__(self):
        super().__init__()
        m = (_B or {}).get("markets", {}).get(self.market) or {}
        self.reds = m.get("reds")
        self.regime = m.get("regime")
        if not (self.reds and self.regime):
            self.ok = False

    def _mu_total(self, comp, mu_cal):
        r = (self.regime.get("r_liga") or {}).get(comp, self.regime.get("r_global", 1.0))
        lam = (self.reds.get("lambda_liga") or {}).get(comp, self.reds.get("lambda_global", 0.245))
        rho = (self.reds.get("rho_liga") or {}).get(comp, 1.0)
        w = (self.reds.get("w_liga") or {}).get(comp, self.reds.get("w_global", 1.62))
        t = self.reds.get("tempero") or {}
        mult = 1.0
        ybar = (self.reds.get("y_bar_liga") or {}).get(comp)
        if ybar and ybar > 0 and mu_cal > 0:
            lo, hi = (t.get("clamp") or [0.6, 1.5])[:2]
            mult = math.exp(t.get("b0", 0.0)
                            + t.get("b_espy", 0.0) * math.log(mu_cal / ybar))
            mult = max(lo, min(hi, mult))
        # ⚠️ 03/08 — o r virou RESIDUAL medido no TOTAL (obs_T/pred_T no OOF pós-degrau;
        # ver cards_regime_blocks.regime_residual no repo do site). Escalar só os
        # amarelos deixava a camada de vermelhos no nível pré-degrau (eles caíram −23%
        # a/a): o total certo é r·(amarelos + vermelhos), não r·amarelos + vermelhos.
        return max(0.1, r * (mu_cal + w * lam * rho * mult))

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
        mu_y = max(0.1, self.a + self.b * float(mu_raw))     # amarelos calibrados
        mu_t = self._mu_total(comp, mu_y)                     # cartões (o que a casa paga)
        po, pu, pp = ou_probs_from_cdf(_nb_cdf_size, mu_t, line, self.phi)
        out = price_dict(mu_t, po, pu, pp)
        out["mu_raw"] = float(mu_raw)
        out["mu_cal"] = float(mu_t)
        out["mu_yellows"] = float(mu_y)      # rastro: de onde saiu
        return out


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
