# -*- coding: utf-8 -*-
"""§9 — μ implícito push-aware em linha INTEIRA (openclose / gráfico do Histórico).

Em linha inteira o empate exato (X=L) devolve stake (push) e não entra no de-vig, então o μ
tem que resolver a probabilidade CONDICIONAL aos resultados decididos:
    P(X>L) / (P(X>L)+P(X<L)) = p_devig
A regra antiga resolvia P(X>L)=p_devig, o que inflava o μ ~0,5 em linhas inteiras.
Meia linha não tem push → o valor não pode regredir.
"""
import unittest

from build_openclose import mu_from_pair, p_cond_over, p_over_from_mu, solve_mu


class PushAwareMuTests(unittest.TestCase):
    def test_brief_escanteios_10_example(self):
        # Escanteios 10 @1,92/1,88 → μ push-aware ≈ 10,127 (NÃO 10,625).
        mu = mu_from_pair(10, 1.92, 1.88)
        self.assertIsNotNone(mu)
        self.assertAlmostEqual(mu, 10.127, delta=0.02)

    def test_old_rule_would_have_inflated(self):
        # A regra antiga (P(X>L)=p_devig) daria ~10,625; a nova tem que ficar bem abaixo.
        p_fair = (1 / 1.92) / (1 / 1.92 + 1 / 1.88)
        mu_new = solve_mu(10, p_fair)
        # reconstrói o μ da regra antiga localmente
        lo, hi = 1e-6, 40.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if p_over_from_mu(10, mid) < p_fair:
                lo = mid
            else:
                hi = mid
        mu_old = (lo + hi) / 2
        self.assertAlmostEqual(mu_old, 10.625, delta=0.03)
        self.assertLess(mu_new, mu_old - 0.3)

    def test_half_line_unchanged(self):
        # Meia linha: sem push, o μ push-aware = μ da regra antiga (P(X>L)=p_devig).
        for L in (9.5, 10.5, 5.5):
            p_fair = (1 / 1.9) / (1 / 1.9 + 1 / 1.9)  # 0.5
            mu_new = solve_mu(L, p_fair)
            # regra antiga
            lo, hi = 1e-6, 40.0
            for _ in range(200):
                mid = (lo + hi) / 2
                if p_over_from_mu(L, mid) < p_fair:
                    lo = mid
                else:
                    hi = mid
            mu_old = (lo + hi) / 2
            self.assertAlmostEqual(mu_new, mu_old, delta=1e-3, msg=f"L={L}")

    def test_cond_over_monotonic_in_mu(self):
        # p_cond_over cresce com μ (bisseção válida) — linhas 9 e 10.
        for L in (9, 10):
            prev = -1.0
            for i in range(1, 400):
                mu = i * 0.1
                val = p_cond_over(L, mu)
                self.assertGreaterEqual(val + 1e-12, prev, msg=f"L={L} mu={mu}")
                prev = val

    def test_over_and_under_integer_symmetry(self):
        # over 10 @1,90 e under 10 @1,90 (par simétrico) → μ ≈ 10 (mediana da contagem).
        mu = mu_from_pair(10, 1.90, 1.90)
        self.assertIsNotNone(mu)
        self.assertAlmostEqual(mu, 10.0, delta=0.25)

    def test_lines_9_9p5_10_10p5_ordered(self):
        # Com odds fixas 1,90/1,90 o μ implícito cresce com a linha.
        mus = [mu_from_pair(L, 1.90, 1.90) for L in (9, 9.5, 10, 10.5)]
        self.assertTrue(all(m is not None for m in mus))
        for a, b in zip(mus, mus[1:]):
            self.assertLess(a, b + 1e-6)

    def test_explicit_push_probability_reduces_over_share(self):
        # Numa linha inteira, a massa de push existe: P(X>L)+P(X<L) < 1.
        po = p_over_from_mu(10, 10.0)
        from build_openclose import p_under_from_mu
        pu = p_under_from_mu(10, 10.0)
        self.assertLess(po + pu, 1.0)  # push = 1-(po+pu) > 0
        self.assertGreater(1.0 - (po + pu), 0.05)


if __name__ == "__main__":
    unittest.main()
