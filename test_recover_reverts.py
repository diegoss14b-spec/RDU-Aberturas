# -*- coding: utf-8 -*-
"""Recuperação do que o feeder reverteu (recover_feeder_reverts — 05/09/2026).

O contrato que estes testes trancam: o merge de ticks é UNIÃO (nunca apaga linha
do main), deduplica em 3 níveis (texto, assinatura lógica, mesma observação sem
identidade por contagem no mesmo ts), ordena por ts e é idempotente; ponteiro só
volta se o da working tree for mais velho; e, num repositório que reproduz a
corrida real (avô → snapshot → feeder com `reset --soft` → main andou), o dry-run
não toca nada e o apply grava só o cru — derivado (keys) fica como está.
"""
import json
import subprocess

from recover_feeder_reverts import (
    arquivos_revertidos, classificar, mesclar_ticks, ponteiro_mais_novo_ou_igual, recuperar,
)


def tick(ts, casa="betano", home="a", away="b", mercado="Escanteios", linha=8.5,
         lado="over", odd=1.8, kind="open", sofa_id="111", gid=None, **extra):
    row = {"ts": ts, "kind": kind, "casa": casa, "kickoff": "2026-09-03T20:00:00-0300",
           "home": home, "away": away, "mercado": mercado, "linha": linha, "lado": lado,
           "odd": odd, "sofa_id": sofa_id, "djogo": "2026-09-03",
           "gid": gid or (f"sofa:{sofa_id}" if sofa_id else f"2026-09-03|{home}|{away}")}
    row.update(extra)
    return json.dumps(row, ensure_ascii=False)


T0, T1, T2 = "2026-09-03T14:00:00-0300", "2026-09-03T16:08:18-0300", "2026-09-03T18:30:48-0300"


# ───────────────────────────── merge puro ─────────────────────────────

def test_merge_uniao_ordena_por_ts_e_nao_perde_linha_nova():
    base = [tick(T0, lado="over"), tick(T0, lado="under"), tick(T2, lado="over", odd=1.9, kind="price")]
    perdidas = [tick(T1, lado="over", odd=1.85, kind="price"), tick(T1, lado="under", odd=2.0, kind="price")]
    out, st = mesclar_ticks(base, perdidas)
    assert st["novas"] == 2 and st["novas:price"] == 2
    assert [json.loads(l)["ts"] for l in out] == [T0, T0, T1, T1, T2]
    assert set(base) <= set(out), "linha do main nunca some"


def test_merge_dedupe_exato_e_logico_sofa_id_int_vs_str():
    base = [tick(T0, sofa_id="111")]                       # main: sofa_id "111" (canonizado)
    perdidas = [tick(T0, sofa_id="111"), tick(T0, sofa_id=111), tick(T1, sofa_id=111)]
    out, st = mesclar_ticks(base, perdidas)
    assert st["ja_no_main_exato"] == 1
    assert st["ja_no_main_logico"] == 1                    # mesmo tick, sofa_id int no snapshot
    assert st["novas"] == 1
    assert len(out) == 2


def test_merge_mesma_observacao_com_identidade_divergente_por_contagem():
    # O caso real (estrelabet 03/09 14:53): main guarda o tick com gid LEGADO e nomes
    # crus; o snapshot revertido tem o mesmo tick já canonizado (sofa:15502671, nomes
    # do fixture). Nem gid nem home/away batem — só a contagem no mesmo ts salva.
    legado = tick(T0, casa="estrelabet", home="univ catolica equ", away="sd aucas",
                  sofa_id=None, gid="2026-09-03|univ catolica equ|sd aucas", linha=7.5, odd=1.25)
    outro_jogo = tick(T0, casa="estrelabet", home="cagliari", away="hellas verona",
                      sofa_id="222", linha=7.5, odd=1.25)      # mesma tupla livre, jogo diferente
    canon = tick(T0, casa="estrelabet", home="universidad catolica del ecuador", away="aucas",
                 sofa_id="15502671", linha=7.5, odd=1.25)
    base = [legado, outro_jogo]
    out, st = mesclar_ticks(base, [canon, outro_jogo])
    assert st["ja_no_main_exato"] == 1
    assert st["ja_no_main_identidade_divergente"] == 1
    assert st.get("novas", 0) == 0 and out == base

    # ...mas se o main tem MENOS linhas daquela tupla, a sobra é dado novo de verdade
    out2, st2 = mesclar_ticks([outro_jogo], [canon, outro_jogo])
    assert st2["novas"] == 1 and canon in out2


def test_merge_idempotente_e_json_invalido_preservado():
    base = [tick(T0), "{isto nao e json", tick(T2)]
    perdidas = [tick(T1), "{isto nao e json"]
    out, st = mesclar_ticks(base, perdidas)
    assert st["novas"] == 1 and "{isto nao e json" in out
    assert out.count("{isto nao e json") == 1
    out2, st2 = mesclar_ticks(out, perdidas)
    assert out2 == out and st2.get("novas", 0) == 0


def test_ponteiro_so_volta_se_working_tree_mais_velha():
    wt_novo = json.dumps({"file": "x", "at": "2026-09-05T14:55:22-03:00"})
    snap = json.dumps({"file": "y", "at": "2026-09-03T16:18:00-03:00"})
    assert ponteiro_mais_novo_ou_igual(wt_novo, snap)
    assert not ponteiro_mais_novo_ou_igual(snap, wt_novo)
    assert ponteiro_mais_novo_ou_igual(snap, snap)
    assert ponteiro_mais_novo_ou_igual("nao e json", snap), "na dúvida, não sobrescreve"
    assert ponteiro_mais_novo_ou_igual(json.dumps({"file": "x"}), snap)


def test_classificar():
    assert classificar("data/odds_history/ticks/2026-09-03.jsonl") == "ticks"
    assert classificar("data/odds/_snapshots/betano_full_09f7.jsonl") == "snapshot_odds"
    assert classificar("data/fixtures/_snapshots/sofa_56d0.json") == "snapshot_fixtures"
    assert classificar("data/odds/betano_latest_full.json") == "ponteiro"
    assert classificar("data/fixtures/sofa_latest.json") == "ponteiro"
    assert classificar("data/odds_history/keys/2026-09.p001.json") == "derivado:keys"
    assert classificar("data/odds_history/ledger/_status.json") == "derivado:ledger"
    assert classificar("data/odds/_status/pinnacle.json") == "derivado:_status"
    assert classificar("valor/data/moves.js") == "derivado:valor/data"


# ───────────────────── a corrida real, num repo temporário ─────────────────────

def _git(root, *args):
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _repo_com_a_corrida(tmp_path):
    """avô → snapshot da Mesa → feeder (index velho + reset --soft) → main andou."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    TK = "data/odds_history/ticks/2026-09-03.jsonl"

    # avô
    _w(root, TK, tick(T0, lado="over") + "\n" + tick(T0, lado="under") + "\n")
    _w(root, "data/odds/_snapshots/betano_full_aaa.jsonl", "betano aaa\n")
    _w(root, "data/odds/betano_latest_full.json", json.dumps({"file": "_snapshots/betano_full_aaa.jsonl", "at": "2026-09-03T14:00:00-03:00"}))
    _w(root, "data/odds/pinnacle_latest_full.json", json.dumps({"file": "_snapshots/pinnacle_full_p0.jsonl", "at": "2026-09-03T14:00:00-03:00"}))
    _w(root, "data/odds/_snapshots/pinnacle_full_p0.jsonl", "pinn p0\n")
    _w(root, "data/odds_history/keys/2026-09.json", json.dumps({"k": {"open_odd": 1.8, "n_obs": 1}}))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "avô")
    avo = _git(root, "rev-parse", "HEAD")

    # snapshot da Mesa: +2 ticks (T1), promove betano bbb (poda aaa), promove pinnacle p1
    # (o run do Actions também captura pinnacle), keys ingeridas
    _w(root, TK, (root / TK).read_text() + tick(T1, lado="over", odd=1.85, kind="price") + "\n"
       + tick(T1, lado="under", odd=2.0, kind="price") + "\n")
    (root / "data/odds/_snapshots/betano_full_aaa.jsonl").unlink()
    _w(root, "data/odds/_snapshots/betano_full_bbb.jsonl", "betano bbb\n")
    _w(root, "data/odds/betano_latest_full.json", json.dumps({"file": "_snapshots/betano_full_bbb.jsonl", "at": "2026-09-03T16:08:00-03:00"}))
    _w(root, "data/odds/_snapshots/pinnacle_full_p1.jsonl", "pinn p1\n")
    _w(root, "data/odds/pinnacle_latest_full.json", json.dumps({"file": "_snapshots/pinnacle_full_p1.jsonl", "at": "2026-09-03T16:08:00-03:00"}))
    _w(root, "data/odds_history/keys/2026-09.json", json.dumps({"k": {"open_odd": 1.8, "n_obs": 2}}))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "odds: snapshot [full]")
    snapshot = _git(root, "rev-parse", "HEAD")

    # feeder: ciclo começou no AVÔ (index velho), push rejeitado → reset --soft snapshot + commit
    _git(root, "checkout", "-q", avo)
    _w(root, "data/odds/_snapshots/pinnacle_full_pf.jsonl", "pinn feeder\n")
    (root / "data/odds/_snapshots/pinnacle_full_p0.jsonl").unlink()
    _w(root, "data/odds/pinnacle_latest_full.json", json.dumps({"file": "_snapshots/pinnacle_full_pf.jsonl", "at": "2026-09-03T16:17:00-03:00"}))
    _git(root, "add", "data/odds/pinnacle_latest_full.json", "data/odds/_snapshots/pinnacle_full_pf.jsonl",
         "data/odds/_snapshots/pinnacle_full_p0.jsonl")
    _git(root, "reset", "--soft", snapshot)          # exatamente o push_verificado velho
    _git(root, "commit", "-q", "-m", "pinnacle: feed local")
    feeder = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-B", "main", feeder)

    # main andou depois: +2 ticks (T2), betano ddd (bbb nunca existiu aqui), keys re-ingeridas
    _w(root, TK, (root / TK).read_text() + tick(T2, lado="over", odd=1.9, kind="price") + "\n"
       + tick(T2, lado="under", odd=1.95, kind="price") + "\n")
    _w(root, "data/odds/_snapshots/betano_full_ddd.jsonl", "betano ddd\n")
    _w(root, "data/odds/betano_latest_full.json", json.dumps({"file": "_snapshots/betano_full_ddd.jsonl", "at": "2026-09-03T18:30:00-03:00"}))
    _w(root, "data/odds_history/keys/2026-09.json", json.dumps({"k": {"open_odd": 1.9, "n_obs": 2}}))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "odds: snapshot seguinte")
    return root, avo, snapshot, feeder, TK


def test_arquivos_revertidos_separa_reversao_do_que_o_feeder_mudou(tmp_path):
    root, avo, snapshot, feeder, TK = _repo_com_a_corrida(tmp_path)
    _, rev = arquivos_revertidos(root, feeder, snapshot)
    paths = {p for _, p in rev}
    assert TK in paths
    assert "data/odds/_snapshots/betano_full_bbb.jsonl" in paths
    assert "data/odds/betano_latest_full.json" in paths
    assert "data/odds_history/keys/2026-09.json" in paths
    assert "data/odds/_snapshots/pinnacle_full_p1.jsonl" in paths       # o Actions promoveu, o feeder nunca teve
    # o que o feeder mudou de propósito NÃO é reversão
    assert "data/odds/pinnacle_latest_full.json" not in paths
    assert "data/odds/_snapshots/pinnacle_full_pf.jsonl" not in paths
    assert "data/odds/_snapshots/pinnacle_full_p0.jsonl" not in paths


def test_dry_run_nao_toca_e_apply_grava_so_o_cru(tmp_path):
    root, avo, snapshot, feeder, TK = _repo_com_a_corrida(tmp_path)
    antes_ticks = (root / TK).read_text()
    antes_keys = (root / "data/odds_history/keys/2026-09.json").read_text()

    rel = recuperar(root, [(feeder[:7], snapshot[:7])], apply=False, log=lambda *_: None)
    assert rel["ticks_stats"]["novas"] == 2
    assert rel["ticks_por_dia"]["2026-09-03"] == 2
    assert [p for p, _ in rel["snapshots_restaurar"]] == [
        "data/odds/_snapshots/betano_full_bbb.jsonl", "data/odds/_snapshots/pinnacle_full_p1.jsonl"]
    assert [p for p, _, _ in rel["ponteiros_mantidos"]] == ["data/odds/betano_latest_full.json"]
    assert rel["ponteiros_restaurar"] == []
    assert rel["derivados"]["derivado:keys"] == 1
    assert _git(root, "status", "--porcelain") == "", "dry-run não pode tocar a working tree"

    rel = recuperar(root, [(feeder[:7], snapshot[:7])], apply=True, log=lambda *_: None)
    assert rel["ticks_tocados"] == [TK]
    linhas = [json.loads(l) for l in (root / TK).read_text().splitlines()]
    assert [l["ts"] for l in linhas] == [T0, T0, T1, T1, T2, T2]
    assert (root / "data/odds/_snapshots/betano_full_bbb.jsonl").read_text() == "betano bbb\n"
    assert (root / "data/odds/_snapshots/pinnacle_full_p1.jsonl").read_text() == "pinn p1\n"
    assert (root / "data/odds_history/keys/2026-09.json").read_text() == antes_keys, "derivado fica intacto"
    assert json.loads((root / "data/odds/betano_latest_full.json").read_text())["at"].startswith("2026-09-03T18:30")
    sujos = sorted(l.split(maxsplit=1)[-1] for l in _git(root, "status", "--porcelain").splitlines())
    assert sujos == ["data/odds/_snapshots/betano_full_bbb.jsonl",
                     "data/odds/_snapshots/pinnacle_full_p1.jsonl", TK]
    assert antes_ticks.splitlines() and set(antes_ticks.splitlines()) <= set((root / TK).read_text().splitlines())

    # idempotente: 2ª aplicação não muda nada
    depois = (root / TK).read_text()
    rel2 = recuperar(root, [(feeder[:7], snapshot[:7])], apply=True, log=lambda *_: None)
    assert rel2["ticks_stats"].get("novas", 0) == 0 and rel2["snapshots_restaurar"] == []
    assert (root / TK).read_text() == depois
