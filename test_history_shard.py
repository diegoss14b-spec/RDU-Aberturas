# -*- coding: utf-8 -*-
"""test_history_shard.py — fatiar o documento do mês não pode perder chave NEM reordenar.

Este arquivo é o banco de odds inteiro (open/close/CLV/liquidação). Um erro aqui não
aparece na tela: sumiria silenciosamente do histórico e só o estudo de CLV, meses
depois, notaria. Por isso os testes exigem IGUALDADE EXATA na ida e volta.

A ORDEM tem teste próprio porque foi ela que reprovou o primeiro desenho (fatiar por
casa): `unify_keys_dict` depende da ordem em que as chaves chegam, e reordenar mudou
27 linhas do gráfico de movimentação sem que uma única chave tivesse se perdido.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import history_shard  # noqa: E402
from history_shard import load_month, month_paths, save_month  # noqa: E402


def _rec(odd=1.85, **extra):
    r = {"open_odd": odd, "status": "open", "n_obs": 3}
    r.update(extra)
    return r


class TestFatias(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._max = history_shard.MAX_BYTES

    def tearDown(self):
        history_shard.MAX_BYTES = self._max
        shutil.rmtree(self.dir, ignore_errors=True)

    def _monolito(self, keys, month="2026-07"):
        (self.dir / f"{month}.json").write_text(
            json.dumps(keys, ensure_ascii=False), encoding="utf-8")

    def _grande(self, n=60, month="2026-07"):
        """Documento que obriga várias fatias (MAX_BYTES reduzido)."""
        history_shard.MAX_BYTES = 2000
        orig = {}
        orig["__main_lines__"] = {"x": 1}
        for i in range(n):
            casa = ("betano", "superbet", "7k", "pinnacle")[i % 4]
            orig[f"{casa}|sofa:{i}|Escanteios|9.5|over"] = _rec(1.5 + i / 100)
        self._monolito(orig, month)
        return orig

    def test_reparte_e_volta_identico(self):
        orig = self._grande()
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        self.assertFalse((self.dir / "2026-07.json").exists(), "monólito removido")
        self.assertGreater(len(list(self.dir.glob("2026-07.p*.json"))), 1, "fatiou mesmo")
        self.assertEqual(load_month(self.dir, "2026-07"), orig)

    def test_ORDEM_preservada(self):
        """O invariante que derrubou o desenho por casa: iterar tem que dar o mesmo."""
        orig = self._grande()
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        self.assertEqual(list(load_month(self.dir, "2026-07")), list(orig))

    def test_fatia_respeita_o_teto(self):
        self._grande(n=120)
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        for p in self.dir.glob("2026-07.p*.json"):
            # o teto pode ser estourado só pelo registro que sozinho já é maior
            self.assertLessEqual(p.stat().st_size, history_shard.MAX_BYTES * 1.5, p.name)

    def test_idempotente(self):
        self._grande()
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        antes = load_month(self.dir, "2026-07")
        n, rm, _ = save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        self.assertEqual((n, rm), (0, 0), "2ª gravação sem mudança deve ser no-op")
        self.assertEqual(load_month(self.dir, "2026-07"), antes)

    def test_so_grava_a_fatia_que_mudou(self):
        self._grande(n=80)
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        keys = load_month(self.dir, "2026-07")
        keys["betano|sofa:0|Escanteios|9.5|over"]["open_odd"] = 9.99   # 1ª fatia
        n, _, _ = save_month(self.dir, "2026-07", keys)
        self.assertEqual(n, 1, "só a fatia tocada devia ser reescrita")

    def test_chave_nova_entra_no_fim_sem_embaralhar(self):
        orig = self._grande()
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        keys = load_month(self.dir, "2026-07")
        keys["betano|sofa:999|Escanteios|9.5|over"] = _rec(3.3)
        save_month(self.dir, "2026-07", keys)
        volta = load_month(self.dir, "2026-07")
        self.assertEqual(list(volta), list(orig) + ["betano|sofa:999|Escanteios|9.5|over"])

    def test_uniao_com_chave_repetida_usa_merge(self):
        # defensivo: a mesma chave em 2 fatias (não deve acontecer)
        (self.dir / "2026-07.p001.json").write_text(json.dumps(
            {"betano|sofa:1|X|1.5|over": {"open_odd": 1.9, "status": "open"}}), encoding="utf-8")
        (self.dir / "2026-07.p002.json").write_text(json.dumps(
            {"betano|sofa:1|X|1.5|over": {"open_odd": 1.9, "status": "settled",
                                          "result": 3, "won": True}}), encoding="utf-8")
        u = load_month(self.dir, "2026-07")["betano|sofa:1|X|1.5|over"]
        self.assertEqual(u["status"], "settled", "merge mantém o estado mais avançado")
        self.assertEqual(u["result"], 3)

    def test_monolito_e_fatia_convivem_na_leitura(self):
        self._monolito({"betano|sofa:1|X|1.5|over": _rec()})
        (self.dir / "2026-07.p001.json").write_text(json.dumps(
            {"pinnacle|sofa:1|X|1.5|over": _rec(2.2)}), encoding="utf-8")
        self.assertEqual(len(month_paths(self.dir, "2026-07")), 2)
        self.assertEqual(len(load_month(self.dir, "2026-07")), 2)

    def test_fatia_sobrando_e_removida(self):
        self._grande(n=80)
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        n_antes = len(list(self.dir.glob("2026-07.p*.json")))
        keys = load_month(self.dir, "2026-07")
        for k in [k for k in keys if not k.startswith("__")][40:]:
            del keys[k]
        save_month(self.dir, "2026-07", keys)
        self.assertLess(len(list(self.dir.glob("2026-07.p*.json"))), n_antes)
        self.assertEqual(len(load_month(self.dir, "2026-07")), 41)  # 40 + __main_lines__

    def test_anti_encolhimento_aborta_sem_escrever(self):
        self._grande(n=100)
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        with self.assertRaises(RuntimeError):
            save_month(self.dir, "2026-07", {"betano|sofa:0|Escanteios|9.5|over": _rec()})
        self.assertEqual(len(load_month(self.dir, "2026-07")), 101, "disco intacto")

    def test_meses_diferentes_nao_se_misturam(self):
        self._monolito({"betano|sofa:1|X|1.5|over": _rec()}, month="2026-06")
        self._monolito({"betano|sofa:2|X|1.5|over": _rec()}, month="2026-07")
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        self.assertTrue((self.dir / "2026-06.json").exists(), "junho intocado")
        self.assertEqual(len(load_month(self.dir, "2026-06")), 1)
        self.assertEqual(len(load_month(self.dir, "2026-07")), 1)

    def test_glob_generico_pega_as_fatias(self):
        """Todos os consumidores usam glob('keys/*.json') — as fatias têm que entrar."""
        self._grande()
        save_month(self.dir, "2026-07", load_month(self.dir, "2026-07"))
        vistos = {}
        for p in sorted(self.dir.glob("*.json")):
            vistos.update(json.loads(p.read_text(encoding="utf-8")))
        self.assertEqual(vistos, load_month(self.dir, "2026-07"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
