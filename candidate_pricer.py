# -*- coding: utf-8 -*-
"""candidate_pricer.py — precificadores dos MODELOS NOVOS (candidatos MAE 2026-07-13).

TITULAR do BOARD.valor desde 15/07 (modelos novos promovidos pelo Diego em 14/07;
rollback via FORCE_LEGACY_BOARD=1 no build_board). Mesma interface dos oficiais:
  price(lg, home_id, away_id, line) ->
    {mu, mu_cal, mu_raw, p_over_win, p_under_win, p_push, p_over, p_under} | None

``mu`` e ``mu_cal`` são a média calibrada realmente usada na CDF; ``mu_raw`` é apenas diagnóstico.

Carrega mu bruto pré-computado (bundle candidate_pricer_data.json, keyed por (comp, sofa_id))
e recalcula probabilidades de linhas inteiras/meias via a distribuição congelada:
  mu_cal = a + b*mu_raw   (calibração linear OOF, congelada)
  CDF = Binomial Negativa size-φ (var = mu + mu²/phi); phi None => Poisson.
  Linha inteira: push mass explícita (pricing_math.ou_probs_from_cdf).

Times fora do bundle (promovidos/rebaixados sem amostra) -> None."""
import json, math
from pathlib import Path
from pricing_math import ou_probs_from_cdf, price_dict, cards_pmf, ou_probs_from_pmf, is_half_line, is_integer_line
from datetime import datetime, timezone, timedelta

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

    def _select(self, comp, home_id, away_id, fixture=None, now=None):
        raw=(self.pairs.get(comp) or {}).get(f'{int(home_id)}|{int(away_id)}')
        market_data=(_B or {}).get('markets',{}).get(self.market,{})
        meta={'ref_applied':False,'ref_reason':'no_verified_fixture_override','ref_source':None,
              'model_version':market_data.get('source_version',(_B or {}).get('version')),'model_data_through':
              ((_B or {}).get('markets',{}).get(self.market,{}).get('data_through_by_comp') or {}).get(comp)}
        try:meta['model_age_days']=((now or datetime.now(timezone.utc)).date()-datetime.fromisoformat(meta['model_data_through']).date()).days
        except (TypeError,ValueError):meta['model_age_days']=None
        if raw is None:return None,meta
        if self.market not in ('cards','fouls'):
            meta['ref_reason']='market_without_referee_adjustment';return raw,meta
        if not fixture or not all(fixture.get(k) is not None for k in ('eid','comp','home_id','away_id','date')):
            meta['ref_reason']='fixture_identity_missing';return raw,meta
        override=((_B or {}).get('fixture_ref_overrides') or {}).get(str(fixture['eid']))
        if not override:return raw,meta
        expected=(comp,int(home_id),int(away_id),str(fixture['date'])[:10])
        received=(override.get('comp'),override.get('home_id'),override.get('away_id'),override.get('date'))
        supplied=(fixture['comp'],int(fixture['home_id']),int(fixture['away_id']),str(fixture['date'])[:10])
        if received!=expected or supplied!=expected:
            meta['ref_reason']='fixture_fingerprint_mismatch';return raw,meta
        if not fixture.get('kickoff_ts') or not override.get('kickoff_ts') or abs(float(fixture['kickoff_ts'])-float(override['kickoff_ts']))>300:
            meta['ref_reason']='fixture_kickoff_mismatch';return raw,meta
        if override.get('version')!=(_B or {}).get('version'):
            meta['ref_reason']='ref_override_version_mismatch';return raw,meta
        try:
            observed=datetime.fromisoformat(override['observed_at'])
            if observed.tzinfo is None:observed=observed.replace(tzinfo=timezone(timedelta(hours=-3)))
            instant=now or datetime.now(timezone.utc)
            age=(instant-observed).total_seconds()/3600
            if age<-.1 or age>48:raise ValueError('stale referee feed')
        except (ValueError,TypeError,KeyError):
            meta['ref_reason']='ref_override_stale';return raw,meta
        selected=(override.get('markets') or {}).get(self.market)
        if not selected or selected.get('mu_model') is None:
            meta['ref_reason']='referee_not_supported_for_market';return raw,meta
        meta.update(ref_applied=True,ref_reason='verified_fixture_referee',ref_source=override.get('source'),
                    referee=override.get('referee'),ref_key=selected.get('ref_key'),ref_observed_at=override['observed_at'])
        return float(selected['mu_model']),meta

    def price(self, lg, home_id, away_id, line, *, fixture=None, bookmaker=None, now=None):
        if not self.ok or home_id is None or away_id is None:
            return None
        if not math.isfinite(float(line)) or not (is_integer_line(line) or is_half_line(line)):
            return None
        comp = self.xwalk.get(lg, lg)
        cp = self.pairs.get(comp)
        if not cp:
            return None
        mu_raw,meta = self._select(comp,home_id,away_id,fixture,now)
        if mu_raw is None:
            return None
        mu_cal = max(0.1, self.a + self.b * float(mu_raw))
        po, pu, pp = ou_probs_from_cdf(_nb_cdf_size, mu_cal, line, self.phi)
        out = price_dict(mu_cal, po, pu, pp)
        out["mu_raw"] = float(mu_raw)
        out["mu_cal"] = float(mu_cal)
        out.update(meta,math_version='nb-poisson-v1')
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
        self.contracts = m.get('card_contracts') or {}
        if not (self.reds and self.regime and self.contracts):
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

    def price(self, lg, home_id, away_id, line, *, fixture=None, bookmaker=None, now=None):
        if not self.ok or home_id is None or away_id is None:
            return None
        if not math.isfinite(float(line)) or not (is_integer_line(line) or is_half_line(line)) or abs(float(line))>100:
            return None
        comp = self.xwalk.get(lg, lg)
        cp = self.pairs.get(comp)
        if not cp:
            return None
        mu_raw,meta = self._select(comp,home_id,away_id,fixture,now)
        if mu_raw is None:
            return None
        book=str(bookmaker or '').casefold().replace(' ','').replace('.','')
        rules=((_B or {}).get('markets',{}).get('cards',{}).get('settlement_by_book') or {})
        rule=rules.get(book) if bookmaker else 'R2'
        c=(self.contracts.get(comp) or {}).get(rule)
        if not c:return None
        cal=((_B or {}).get('markets',{}).get('cards',{}).get('cal_by_comp') or {}).get(comp) or {'a':self.a,'b':self.b,'phi':self.phi}
        mu_y = max(0.1,float(cal['a'])+float(cal['b'])*float(mu_raw))
        mult=1.0
        if c.get('ybar') and mu_y>0:
            mult=math.exp(c.get('b0',0)+c.get('b_espy',0)*math.log(mu_y/c['ybar']))
            lo,hi=c.get('clamp',[.6,1.5]);mult=max(lo,min(hi,mult))
        lam=c['lam_base']*mult;frac=max(0,min(1,c['w']-1))
        lam_d,lam_s=lam*frac,lam*(1-frac)
        mu_y_adjusted=mu_y*c['r'];mu_t=mu_y_adjusted+2*lam_d+lam_s
        pmf=cards_pmf(mu_y_adjusted,lam_d,lam_s,cal.get('phi'),max(40,int(math.floor(line))+20))
        po, pu, pp = ou_probs_from_pmf(pmf,line)
        out = price_dict(mu_t, po, pu, pp)
        out["mu_raw"] = float(mu_raw)
        out["mu_cal"] = float(mu_t)
        out["mu_yellows"] = float(mu_y_adjusted)
        out.update(meta,math_version='cards-convolution-v1',settlement_rule=rule,
                   contract_source=c.get('source'),lambda_direct=lam_d,lambda_second_yellow=lam_s)
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
