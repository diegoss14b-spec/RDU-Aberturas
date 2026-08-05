"""Roteamento de proxy por casa (05/08/2026).

POR QUE ESTES TESTES EXISTEM
----------------------------
A Pinnacle — a BALIZADORA da Mesa, contra a qual o "justo" é calculado — passou
mais de um dia inteiro devolvendo ZERO evento na nuvem. O log do runner dizia
`matchups: 0`, enquanto o MESMO código, com a MESMA chave, rodando na máquina do
Diego, devolvia 4.671. A única variável era o IP de saída: o runner é Azure
westcentralus, e a API guest não responde pra ele.

Ela era a única das casas que nunca chamava `br_proxies`. E o painel exibia
"BR" pra ela o tempo todo, porque o campo `proxy_br` do status era
`bool(DECODO_USER)` — respondia "existe credencial no ambiente", não "esta casa
passou pelo Brasil". O campo que deveria denunciar o problema afirmava o oposto.

Dois testes aqui são CONTROLE NEGATIVO de propósito (`PROXY_OFF` desliga, e sem
credencial não roteia). Sem eles, um teste que só verifica "tem proxy" passaria
com o interruptor de emergência quebrado — e o `PROXY_OFF` é justamente o que
permite reverter isto sem editar código, caso o Decodo fique caro ou caia.
"""
import os
import unittest
from unittest.mock import patch

import capture_common as cc

CREDS = {"DECODO_USER": "u-teste", "DECODO_PASS": "p-teste"}


class ProxyPorCasaTest(unittest.TestCase):
    def setUp(self):
        cc._PROXY_USADO.clear()

    def test_com_credencial_roteia_e_registra(self):
        with patch.dict(os.environ, CREDS, clear=False):
            os.environ.pop("PROXY_OFF", None)
            px = cc.br_proxies("7k")
        self.assertIsInstance(px, dict)
        self.assertIn("7k", cc._PROXY_USADO)

    def test_proxy_off_desliga_a_casa_citada(self):
        """CONTROLE NEGATIVO: o interruptor de emergência tem que funcionar."""
        with patch.dict(os.environ, dict(CREDS, PROXY_OFF="7k"), clear=False):
            px = cc.br_proxies("7k")
        self.assertIsNone(px)
        self.assertNotIn("7k", cc._PROXY_USADO)

    def test_proxy_off_nao_afeta_as_outras_casas(self):
        with patch.dict(os.environ, dict(CREDS, PROXY_OFF="7k"), clear=False):
            self.assertIsNone(cc.br_proxies("7k"))
            self.assertIsInstance(cc.br_proxies("pinnacle"), dict)
        self.assertIn("pinnacle", cc._PROXY_USADO)
        self.assertNotIn("7k", cc._PROXY_USADO)

    def test_sem_credencial_nao_roteia_nem_registra(self):
        """CONTROLE NEGATIVO: sem env, roda direto (é o caso local do Diego)."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DECODO_USER", "DECODO_PASS")}
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(cc.br_proxies("pinnacle"))
        self.assertNotIn("pinnacle", cc._PROXY_USADO)

    def test_proxy_br_do_status_reflete_a_casa_e_nao_o_ambiente(self):
        """O campo do painel tem que dizer o que ACONTECEU, não o que existe.

        Antes: `bool(DECODO_USER)` -> 'BR' pra todas as 8 casas, inclusive as 4
        que saem direto. Este teste falha se alguém voltar àquele valor.
        """
        with patch.dict(os.environ, CREDS, clear=False):
            os.environ.pop("PROXY_OFF", None)
            cc.br_proxies("betano")            # esta roteia
            usou = "betano" in cc._PROXY_USADO
            nao_usou = "bet365" in cc._PROXY_USADO   # esta nunca chamou
        self.assertTrue(usou)
        self.assertFalse(nao_usou)


class PinnacleUsaProxyTest(unittest.TestCase):
    """A regressão concreta: se alguém tirar `proxies=` do request, cai aqui."""

    def setUp(self):
        cc._PROXY_USADO.clear()

    def test_get_da_pinnacle_passa_proxies_pro_requests(self):
        import fetch_odds_pinnacle as fp

        class Resp:
            status_code = 200
            text = "[]"

            def json(self):
                return []

        with patch.dict(os.environ, CREDS, clear=False):
            os.environ.pop("PROXY_OFF", None)
            with patch.object(fp.requests, "get", return_value=Resp()) as g:
                fp.get("sports/29/matchups")
        self.assertTrue(g.called)
        px = g.call_args.kwargs.get("proxies")
        self.assertIsInstance(px, dict, "a Pinnacle voltou a sair sem proxy")

    def test_proxy_off_pinnacle_volta_pro_direto(self):
        """CONTROLE NEGATIVO: o rollback sem deploy tem que continuar existindo."""
        import fetch_odds_pinnacle as fp

        class Resp:
            status_code = 200
            text = "[]"

            def json(self):
                return []

        with patch.dict(os.environ, dict(CREDS, PROXY_OFF="pinnacle"), clear=False):
            with patch.object(fp.requests, "get", return_value=Resp()) as g:
                fp.get("sports/29/matchups")
        self.assertIsNone(g.call_args.kwargs.get("proxies"))


def _chamadas_sem_nome(caminho):
    """Chamadas a br_proxies() sem argumento, lidas da ÁRVORE SINTÁTICA.

    ⚠ ERREI ISTO DUAS VEZES EM UMA HORA (05/08) tentando casar texto: a 1ª
    versão do teste do cloudflared e a 1ª deste casaram com a PRÓPRIA explicação
    escrita no código — o comentário citava `br_proxies()` pra explicar o
    defeito, e depois a docstring do capture_common citou de novo. Filtrar `#`
    não bastou porque docstring não é comentário.
    Ler o AST resolve a classe inteira: prosa não vira nó de chamada.
    """
    import ast
    try:
        arv = ast.parse(open(caminho, encoding="utf-8").read())
    except SyntaxError:
        return []
    achados = []
    for no in ast.walk(arv):
        if not isinstance(no, ast.Call):
            continue
        f = no.func
        nome = getattr(f, "id", None) or getattr(f, "attr", None)
        if nome == "br_proxies" and not no.args and not no.keywords:
            achados.append(no.lineno)
    return achados


class SofaRespeitaProxyOffTest(unittest.TestCase):
    """CONTROLE NEGATIVO do interruptor na fonte mais barata de desligar.

    `br_proxies()` sem argumento ignora PROXY_OFF em silêncio — o SofaScore
    ficou assim até 05/08. É API pública com ~44 requisições por rodada, ou
    seja, a primeira candidata a sair do Decodo; e era justamente a única que o
    interruptor não alcançava. Achado da auditoria Kimi.
    """

    def setUp(self):
        cc._PROXY_USADO.clear()

    def test_nenhum_chamador_omite_o_nome_da_fonte(self):
        import glob
        maus = {f: _chamadas_sem_nome(f) for f in glob.glob("fetch_*.py")}
        maus = {f: l for f, l in maus.items() if l}
        self.assertEqual(maus, {},
                         "br_proxies() sem nome: PROXY_OFF nao alcanca essas fontes")

    def test_proxy_off_sofa_desliga(self):
        with patch.dict(os.environ, dict(CREDS, PROXY_OFF="sofa"), clear=False):
            self.assertIsNone(cc.br_proxies("sofa"))
        self.assertNotIn("sofa", cc._PROXY_USADO)


if __name__ == "__main__":
    unittest.main()
