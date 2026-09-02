# -*- coding: utf-8 -*-
"""Fatiamento dos JSONL mensais append-only (jsonl_shard — GH001, 02/09/2026).

O contrato que estes testes trancam: monólito do mês congela no teto, a escrita
rola pra fatias pNNN, lote maior que o teto é dividido, união preserva tudo, e
NADA já gravado é reescrito (ledger imutável).
"""
import json

from jsonl_shard import append_jsonl_month, month_jsonl_paths


def linha(i, pad=0):
    return json.dumps({"key": f"k{i:04d}", "pad": "x" * pad})


def test_mes_novo_escreve_no_monolito(tmp_path):
    tocados = append_jsonl_month(tmp_path, "2026-09", [linha(1), linha(2)])
    assert tocados == [tmp_path / "2026-09.jsonl"]
    assert (tmp_path / "2026-09.jsonl").read_text().splitlines() == [linha(1), linha(2)]
    assert month_jsonl_paths(tmp_path, "2026-09") == [tmp_path / "2026-09.jsonl"]


def test_rola_no_teto_e_divide_o_lote(tmp_path):
    linhas = [linha(i, pad=80) for i in range(40)]
    cap = 350
    tocados = append_jsonl_month(tmp_path, "2026-08", linhas, max_bytes=cap)
    paths = month_jsonl_paths(tmp_path, "2026-08")
    assert paths[0].name == "2026-08.jsonl"
    assert len(paths) > 1, "era pra ter rolado fatia"
    assert [p.name for p in paths[1:]] == [
        f"2026-08.p{i:03d}.jsonl" for i in range(1, len(paths))
    ]
    assert all(p.stat().st_size <= cap for p in paths)
    # união das fatias = o lote inteiro, na ordem
    assert [l for p in paths for l in p.read_text().splitlines()] == linhas
    assert tocados == paths


def test_congela_monolito_gordo_e_continua_na_ultima_fatia(tmp_path):
    cap = 300
    gordo = tmp_path / "2026-08.jsonl"
    gordo.write_text("x" * 400 + "\n")      # o caso real: monólito já acima do teto
    antes = gordo.read_text()
    append_jsonl_month(tmp_path, "2026-08", [linha(1)], max_bytes=cap)
    assert gordo.read_text() == antes        # congelou — nunca mais é tocado
    p1 = tmp_path / "2026-08.p001.jsonl"
    assert p1.read_text().splitlines() == [linha(1)]
    # segunda escrita continua na MESMA fatia enquanto couber
    append_jsonl_month(tmp_path, "2026-08", [linha(2)], max_bytes=cap)
    assert p1.read_text().splitlines() == [linha(1), linha(2)]
    assert not (tmp_path / "2026-08.p002.jsonl").exists()


def test_linha_maior_que_o_teto_nao_trava(tmp_path):
    giga = linha(1, pad=1000)
    append_jsonl_month(tmp_path, "2026-09", [giga, linha(2)], max_bytes=200)
    paths = month_jsonl_paths(tmp_path, "2026-09")
    assert len(paths) == 2                   # gigante ocupa a fatia sozinha, resto rola
    assert paths[0].read_text().splitlines() == [giga]
    assert paths[1].read_text().splitlines() == [linha(2)]


def test_aceita_dicts(tmp_path):
    append_jsonl_month(tmp_path, "2026-09", [{"a": 1}])
    assert json.loads((tmp_path / "2026-09.jsonl").read_text()) == {"a": 1}


def test_numeracao_continua_da_maior_fatia(tmp_path):
    (tmp_path / "2026-08.jsonl").write_text("x" * 400 + "\n")
    (tmp_path / "2026-08.p001.jsonl").write_text("x" * 400 + "\n")
    (tmp_path / "2026-08.p002.jsonl").write_text("y\n")
    append_jsonl_month(tmp_path, "2026-08", [linha(9)], max_bytes=300)
    # p002 tinha espaço → continua nela; nova fatia nasceria como p003, nunca p001
    assert (tmp_path / "2026-08.p002.jsonl").read_text().splitlines() == ["y", linha(9)]
    assert not (tmp_path / "2026-08.p003.jsonl").exists()


def test_lote_vazio_nao_cria_arquivo(tmp_path):
    assert append_jsonl_month(tmp_path, "2026-09", []) == []
    assert month_jsonl_paths(tmp_path, "2026-09") == []
