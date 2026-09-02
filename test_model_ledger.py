# -*- coding: utf-8 -*-
"""Regressões do registro forward modelo×odds (build_model_ledger, 31/07)."""
import json
import tempfile
from datetime import datetime
from pathlib import Path

from build_model_ledger import (
    BRT,
    LABEL2COMP,
    emit_ledger,
    is_integer_line,
    load_bundle,
    load_push_semantics,
    stamp_records,
)

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=BRT)
FUTURO = "2026-08-05T16:00:00-03:00"


class StubPricer:
    ok = True

    def price(self, comp, hid, aid, line):
        return {"mu": 4.2, "mu_cal": 4.2, "mu_raw": 4.0,
                "p_over_win": 0.55, "p_under_win": 0.4, "p_push": 0.05,
                "p_over": 0.55, "p_under": 0.4}


class NonePricer(StubPricer):
    def price(self, comp, hid, aid, line):
        return None                      # par fora do bundle


def stub_pricers(cls=StubPricer):
    return {m: cls() for m in ("cards", "fouls", "shots", "corners")}


def rec(status="closed", kickoff=FUTURO, **extra):
    base = {
        "status": status, "kickoff": kickoff,
        "open_odd": 1.85, "open_ts": "2026-07-30T10:00:00-03:00",
        "close_odd": 1.9, "close_ts": "2026-08-05T15:00:00-03:00",
        "min_odd": 1.8, "max_odd": 1.95, "n_moves": 3,
        "capture_quality": "full_prematch",
        "home_raw": "Time Alpha", "away_raw": "Time Beta",
        "sofa_id": "555", "settlement_source": "auto",
    }
    base.update(extra)
    return base


def fixidx(label="BR-A"):
    return {"555": {"h": 10, "a": 20, "lb": label, "ko": FUTURO}}


def test_carimbo_pre_kickoff_e_unico():
    merged = {"betano|sofa:555|Cartões|4.5|over": rec()}
    n = stamp_records(merged, fixidx(), stub_pricers(), "2026-07-28", "d24cd180", NOW)
    r = merged["betano|sofa:555|Cartões|4.5|over"]
    assert n["carimbadas"] == 1
    assert r["m_p_over"] == 0.55 and r["m_mu"] == 4.2 and r["m_mu_raw"] == 4.0
    assert r["m_ver"] == "2026-07-28" and r["m_comp"] == "BR-A"
    assert r["m_pre_ko"] is True

    # carimbo é ÚNICO: retreino posterior não re-carimba
    n2 = stamp_records(merged, fixidx(), stub_pricers(), "2026-08-01", "ffffffff", NOW)
    assert n2["carimbadas"] == 0 and r["m_ver"] == "2026-07-28"


def test_trava_anti_vazamento_pos_kickoff():
    passado = "2026-07-20T19:00:00-03:00"
    key = "betano|sofa:555|Faltas|26.5|over"
    # bundle de retreino POSTERIOR ao jogo → bloqueado
    merged = {key: rec(kickoff=passado)}
    n = stamp_records(merged, fixidx(), stub_pricers(), "2026-07-28", "x", NOW)
    assert n["pos_ko_bloqueado_pela_versao"] == 1
    assert "m_ts" not in merged[key]
    # bundle do MESMO dia do jogo → bloqueado (ataque 31/07: o retreino da
    # versão X inclui jogos do dia X — o jogo podia estar na base de treino)
    merged = {key: rec(kickoff=passado)}
    n = stamp_records(merged, fixidx(), stub_pricers(), "2026-07-20", "x", NOW)
    assert n["pos_ko_bloqueado_pela_versao"] == 1
    assert "m_ts" not in merged[key]
    # bundle ESTRITAMENTE anterior ao jogo → carimba (caso B1: sofa_id tardio)
    merged = {key: rec(kickoff=passado)}
    n = stamp_records(merged, fixidx(), stub_pricers(), "2026-07-19", "x", NOW)
    assert n["carimbadas"] == 1 and merged[key]["m_pre_ko"] is False


def test_buraco_sem_par_no_bundle_carimba_null_e_pode_preencher_pre_ko():
    key = "betano|sofa:555|Finalizações|24.5|under"
    merged = {key: rec()}
    n = stamp_records(merged, fixidx(), stub_pricers(NonePricer), "2026-07-28", "x", NOW)
    assert n["carimbadas_sem_par_no_bundle"] == 1
    assert merged[key]["m_p_over"] is None and merged[key]["m_ver"] == "2026-07-28"
    # bundle ganhou o par antes do kickoff → o buraco é preenchido
    n2 = stamp_records(merged, fixidx(), stub_pricers(), "2026-07-30", "y", NOW)
    assert n2["carimbadas"] == 1 and merged[key]["m_p_over"] == 0.55
    assert merged[key]["m_ver"] == "2026-07-30"


def test_label_fora_do_mapa_nao_carimba():
    merged = {"betano|sofa:555|Escanteios|9.5|over": rec()}
    n = stamp_records(merged, fixidx(label="WC"), stub_pricers(), "2026-07-28", "x", NOW)
    assert n["label_fora_do_mapa"] == 1
    assert "m_ts" not in merged["betano|sofa:555|Escanteios|9.5|over"]


def test_sofa_id_settle_tambem_carimba():
    key = "betano|2026-08-05|time alpha|time beta|Cartões|4.5|over"
    merged = {key: rec(sofa_id=None, sofa_id_settle=555)}
    n = stamp_records(merged, fixidx(), stub_pricers(), "2026-07-28", "x", NOW)
    assert n["carimbadas"] == 1 and merged[key]["m_comp"] == "BR-A"


def test_handicap_e_outros_mercados_ficam_fora():
    merged = {
        "bet365|sofa:555|Handicap de Cartões|-1.5|casa": rec(),
        "betano|sofa:555|Chutes no gol|8.5|over": rec(),
    }
    n = stamp_records(merged, fixidx(), stub_pricers(), "2026-07-28", "x", NOW)
    assert n["carimbadas"] == 0
    assert all("m_ts" not in r for r in merged.values())


def carimbada_settled(**rec_kwargs):
    base = rec(status="settled", result=5.0, won=True,
               result_yellows=4.0, result_reds=1.0,
               m_ts="2026-07-30T12:00:00-03:00", m_ver="2026-07-28",
               m_sha8="d24cd180", m_comp="BR-A", m_pre_ko=True,
               m_p_over=0.55, m_p_push=0.05, m_mu=4.2, m_mu_raw=4.0)
    base.update(rec_kwargs)
    return base


def test_emit_idempotente_e_dedupe_por_releitura():
    key = "bet365|sofa:555|Cartões|4.0|over"
    push = load_push_semantics()
    with tempfile.TemporaryDirectory() as tmp:
        merged = {key: carimbada_settled()}
        n1 = emit_ledger(merged, fixidx(), push, {"cards": "neutral"}, NOW, ledger_dir=tmp)
        assert n1["emitidas"] == 1 and merged[key]["m_emitted"]
        # 2ª rodada: nada novo
        n2 = emit_ledger(merged, fixidx(), push, {"cards": "neutral"}, NOW, ledger_dir=tmp)
        assert n2.get("emitidas", 0) == 0

        # crash entre append e mark: linha no arquivo, record sem m_emitted →
        # a releitura do mês só marca, NÃO duplica
        merged[key].pop("m_emitted")
        n3 = emit_ledger(merged, fixidx(), push, {"cards": "neutral"}, NOW, ledger_dir=tmp)
        assert n3["ja_no_arquivo_so_marcadas"] == 1 and n3.get("emitidas", 0) == 0

        month = FUTURO[:7]
        lines = [json.loads(l) for l in
                 (Path(tmp) / f"{month}.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 1
        row = lines[0]
        assert row["key"] == key and row["p_over"] == 0.55 and row["mu"] == 4.2
        assert row["market"] == "cards" and row["mercado"] == "Cartões"
        assert row["sofa_id"] == 555 and row["id_origem"] == "sofa"
        assert row["home_id"] == 10 and row["away_id"] == 20
        assert row["result"] == 5.0 and row["won"] is True
        assert row["result_yellows"] == 4.0 and row["result_reds"] == 1.0
        assert row["model_version"] == "2026-07-28"
        # open era pré-kickoff, close também (odd válida antes do apito)
        assert row["open_pre_ko"] is True and row["close_pre_ko"] is True


def test_push_sem_por_casa_e_linha():
    push = load_push_semantics()
    with tempfile.TemporaryDirectory() as tmp:
        merged = {
            "bet365|sofa:555|Cartões|4.0|over": carimbada_settled(),
            "pinnacle|sofa:555|Cartões|4.0|under": carimbada_settled(),
            "betano|sofa:555|Cartões|4.0|over": carimbada_settled(),
            "betano|sofa:555|Cartões|4.5|over": carimbada_settled(),
        }
        emit_ledger(merged, fixidx(), push, {}, NOW, ledger_dir=tmp)
        rows = {json.loads(l)["key"]: json.loads(l) for l in
                (Path(tmp) / f"{FUTURO[:7]}.jsonl").read_text(encoding="utf-8").splitlines()}
        assert rows["bet365|sofa:555|Cartões|4.0|over"]["push_sem"] == "counts_against"
        assert rows["pinnacle|sofa:555|Cartões|4.0|under"]["push_sem"] == "conditional_no_push"
        assert rows["betano|sofa:555|Cartões|4.0|over"]["push_sem"] == "unknown"
        meia = rows["betano|sofa:555|Cartões|4.5|over"]
        assert meia["linha_inteira"] is False and meia["push_sem"] is None
        inteira = rows["betano|sofa:555|Cartões|4.0|over"]
        assert inteira["linha_inteira"] is True


def test_emit_sem_carimbo_ou_sem_settle_nao_sai():
    push = load_push_semantics()
    with tempfile.TemporaryDirectory() as tmp:
        merged = {
            "betano|sofa:555|Cartões|4.5|over": rec(status="settled", result=5.0),  # sem carimbo
            "betano|sofa:555|Faltas|26.5|over": carimbada_settled(status="closed"),  # sem settle
        }
        n = emit_ledger(merged, fixidx(), push, {}, NOW, ledger_dir=tmp)
        assert n.get("emitidas", 0) == 0
        assert not (Path(tmp) / f"{FUTURO[:7]}.jsonl").exists()


def test_paridade_com_o_pricer_do_board():
    """O carimbo tem que ser EXATAMENTE o candidate_pricer.price() do board
    (mesmo bundle, mesmo comp, mesmo par) — prova com o bundle real do repo."""
    pricers, version, sha8, _rp = load_bundle()
    assert pricers is not None, "bundle do repo ausente/ilegível"
    pr = pricers["cards"]
    comp = "BR-A"
    pair = next(iter(pr.pairs[comp]))
    hid, aid = (int(x) for x in pair.split("|"))
    direto = pr.price(comp, hid, aid, 4.5)
    assert direto is not None

    key = "betano|sofa:777|Cartões|4.5|over"
    merged = {key: rec(sofa_id="777")}
    idx = {"777": {"h": hid, "a": aid, "lb": "BR-A", "ko": FUTURO}}
    n = stamp_records(merged, idx, pricers, version, sha8, NOW)
    assert n["carimbadas"] == 1
    r = merged[key]
    assert r["m_p_over"] == round(direto["p_over_win"], 4)
    assert r["m_p_push"] == round(direto["p_push"], 4)
    assert r["m_mu"] == round(direto["mu_cal"], 3)
    assert r["m_mu_raw"] == round(direto["mu_raw"], 4)
    assert r["m_ver"] == version and r["m_sha8"] == sha8


def test_label2comp_cobre_todas_as_fixtures():
    """Toda liga do fetch_fixtures (menos as encerradas de propósito) tem comp."""
    from fetch_fixtures_sofascore import TOURNAMENTS
    sem_comp = {label for _tid, label in TOURNAMENTS if label not in LABEL2COMP}
    assert sem_comp <= {"WC"}, f"labels sem comp no ledger: {sorted(sem_comp)}"


def test_is_integer_line():
    assert is_integer_line(4.0) and is_integer_line("5")
    assert not is_integer_line(4.5) and not is_integer_line("x") and not is_integer_line(None)


def test_emit_rola_fatia_no_teto_e_dedupa_entre_fatias(monkeypatch):
    """GH001 (02/09): rajada de emissão maior que o teto rola pra fatias pNNN e o
    dedupe pós-crash cobre TODAS as fatias do mês, não só o monólito."""
    import jsonl_shard
    from jsonl_shard import month_jsonl_paths

    monkeypatch.setattr(jsonl_shard, "MAX_BYTES", 900)   # ~1 linha de ledger por fatia
    push = load_push_semantics()
    keys = [f"betano|sofa:555|Cartões|{l}.5|over" for l in range(3, 9)]
    with tempfile.TemporaryDirectory() as tmp:
        merged = {k: carimbada_settled() for k in keys}
        n1 = emit_ledger(merged, fixidx(), push, {}, NOW, ledger_dir=tmp)
        assert n1["emitidas"] == len(keys)
        month = FUTURO[:7]
        paths = month_jsonl_paths(Path(tmp), month)
        assert len(paths) > 1, "era pra ter rolado fatia com o teto de teste"
        rows = [json.loads(l) for p in paths
                for l in p.read_text(encoding="utf-8").splitlines()]
        assert sorted(r["key"] for r in rows) == sorted(keys)

        # crash entre append e mark (marks perdidos): a releitura dedupa contra
        # a UNIÃO das fatias — nada é re-emitido nem duplicado
        for k in keys:
            merged[k].pop("m_emitted")
        n2 = emit_ledger(merged, fixidx(), push, {}, NOW, ledger_dir=tmp)
        assert n2.get("emitidas", 0) == 0
        assert n2["ja_no_arquivo_so_marcadas"] == len(keys)
        rows2 = [l for p in month_jsonl_paths(Path(tmp), month)
                 for l in p.read_text(encoding="utf-8").splitlines()]
        assert len(rows2) == len(rows)
