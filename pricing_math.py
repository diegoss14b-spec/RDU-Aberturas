# -*- coding: utf-8 -*-
"""pricing_math.py — probabilidades O/U com push e EV corretos (brief auditoria 2026-07-14).

Contrato para linha total inteira L (contagem discreta X):
  p_over_win  = P(X > L)
  p_under_win = P(X < L)
  p_push      = P(X = L)

Para meia linha (L = n + 0.5):
  p_push = 0
  p_over_win  = P(X >= n+1) = 1 - CDF(n)
  p_under_win = P(X <= n)   = CDF(n)

EV por unidade apostada (stake devolvida no push):
  EV = p_win * odd + p_push - 1

Odd de break-even com push:
  fair_odd = (1 - p_push) / p_win

Edge: compara prob. condicionais aos resultados decididos (exclui push):
  p_cond = p_win / (1 - p_push)
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Optional, Tuple


def is_half_line(line: float, tol: float = 1e-9) -> bool:
    L = float(line)
    return abs(L - math.floor(L) - 0.5) < tol


def is_integer_line(line: float, tol: float = 1e-9) -> bool:
    L = float(line)
    return abs(L - round(L)) < tol


def ou_probs_from_cdf(
    cdf: Callable[..., float],
    mu: float,
    line: float,
    *cdf_args,
) -> Tuple[float, float, float]:
    """Retorna (p_over_win, p_under_win, p_push) via CDF(k) = P(X <= k)."""
    L = float(line)
    mu = float(mu)
    if mu <= 0:
        return 0.0, 1.0, 0.0

    if is_half_line(L):
        k = math.floor(L)  # n for n.5
        p_under = float(cdf(k, mu, *cdf_args))
        p_over = 1.0 - p_under
        return _clip3(p_over, p_under, 0.0)

    if not is_integer_line(L):
        # Linha não-padrão (ex. 7.25): trata como “sem push mass” no ponto arredondado
        # usando over = 1-CDF(floor(L)), under = CDF(floor(L))  (igual meia para fins práticos).
        k = math.floor(L)
        p_under = float(cdf(k, mu, *cdf_args))
        p_over = 1.0 - p_under
        return _clip3(p_over, p_under, 0.0)

    k = int(round(L))
    cdf_k = float(cdf(k, mu, *cdf_args))
    cdf_km1 = float(cdf(k - 1, mu, *cdf_args)) if k >= 0 else 0.0
    p_push = max(0.0, cdf_k - cdf_km1)
    p_over = max(0.0, 1.0 - cdf_k)          # X > L
    p_under = max(0.0, cdf_km1)             # X < L
    return _clip3(p_over, p_under, p_push)


def _clip3(po: float, pu: float, pp: float) -> Tuple[float, float, float]:
    po = min(1.0, max(0.0, po))
    pu = min(1.0, max(0.0, pu))
    pp = min(1.0, max(0.0, pp))
    s = po + pu + pp
    if s <= 0:
        return 0.0, 1.0, 0.0
    if abs(s - 1.0) > 1e-9:
        po, pu, pp = po / s, pu / s, pp / s
    return po, pu, pp


def price_dict(mu: float, p_over: float, p_under: float, p_push: float) -> Dict[str, float]:
    """Dict canônico do pricer (aliases p_over/p_under = win probs, nunca 1-p_over com push)."""
    return {
        "mu": float(mu),
        "p_over_win": float(p_over),
        "p_under_win": float(p_under),
        "p_push": float(p_push),
        # aliases legados — SEMPRE win-only
        "p_over": float(p_over),
        "p_under": float(p_under),
    }


def expected_value(p_win: float, odd: float, p_push: float = 0.0) -> float:
    """EV por unidade de stake (push devolve stake)."""
    return float(p_win) * float(odd) + float(p_push) - 1.0


def fair_odd(p_win: float, p_push: float = 0.0) -> Optional[float]:
    """Odd de break-even; None se p_win ~ 0."""
    pw = float(p_win)
    if pw <= 1e-12:
        return None
    return (1.0 - float(p_push)) / pw


def conditional_win_prob(p_win: float, p_push: float = 0.0) -> float:
    """P(win | não push) para edge vs mercado 2-way."""
    pp = float(p_push)
    if pp >= 1.0 - 1e-12:
        return 0.0
    return float(p_win) / (1.0 - pp)


def edge_vs_market(p_win: float, market_p: float, p_push: float = 0.0) -> float:
    """Edge em probabilidade condicional (modelo) vs de-vig do mercado."""
    return conditional_win_prob(p_win, p_push) - float(market_p)
