# -*- coding: utf-8 -*-
"""Reconciliação barata do persist com o feeder local (persist_reconcile — 05/09/2026).

O contrato que estes testes trancam, com repositórios git TEMPORÁRIOS (bare + clones)
reproduzindo a corrida real:
  * FEEDER_PATHS cobre os add_paths REAIS do feeder_mesa_once.py e do
    pinnacle_feeder_local.sh (o feeder não é tocado; se ele ganhar caminho, este
    teste grita) e NÃO cobre nada que o persist produz (ticks, keys, status das
    outras casas, valor/data/*.js);
  * caso A (feeder empurrou antes): classifica 'feeder' e a reconciliação traz os
    arquivos do feeder (inclusive a REMOÇÃO do snapshot rotacionado) por cima da
    árvore local, mantendo os ticks/js desta rodada — também com o index já
    staged (o retry do persist após `reset --soft HEAD~1`);
  * caso B (outra rodada da Mesa empurrou antes) e força-push: 'outro', e aplica
    RECUSA sem tocar em nada;
  * o persist_snapshot.sh REAL, ponta a ponta: A → sem reingest, main final = ticks
    locais + arquivos do feeder, histórico do feeder preservado (o commit dele é
    ancestral do main); A com o feeder empurrando DE NOVO entre o fetch e o push →
    segunda volta barata; B → cai no reingest (stubs registram "reingest chamado").
"""
import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import persist_reconcile as pr

RAIZ = Path(__file__).resolve().parent
PERSIST_SH = RAIZ / "persist_snapshot.sh"


# ───────────────────────── infra: git isolado do ambiente do usuário ─────────────────────────

def _env(tmp, extra=None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp / "home"),               # sem ~/.gitconfig (hooks, gpgsign, etc.)
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
    }
    if extra:
        env.update(extra)
    return env


def git(cwd, *args, env=None, check=True):
    p = subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false",
                        *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


def escreve(root, rel, texto):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")


def le_no_main(bare, rel, env):
    p = git(bare, "show", f"main:{rel}", env=env, check=False)
    return p.stdout if p.returncode == 0 else None


ARQUIVOS_INICIAIS = {
    "data/odds/pinnacle_latest_full.json": '{"file":"pinnacle_full_aaa.jsonl","n":90,"captured_by":"local"}\n',
    "data/odds/pinnacle_latest.json": '{"file":"pinnacle_full_aaa.jsonl"}\n',
    "data/odds/_snapshots/pinnacle_full_aaa.jsonl": "pinn tick 1\n",
    "data/odds/_snapshots/betano_full_bbb.jsonl": "betano tick 1\n",
    "data/odds/_status/pinnacle.json": '{"n_events":90,"origem":"feeder"}\n',
    "data/odds/_status/superbet.json": '{"n_events":300}\n',
    "data/odds/_status/betano.json": '{"n_events":10}\n',
    "data/odds/_status/summary.json": '{"ts_brt":"2026-09-05 10:00"}\n',
    "data/odds_history/ticks/2026-09-05.jsonl": '{"tick":1}\n',
    "data/odds_history/keys/2026-09.p001.json": '{"k":1}\n',
    "valor/data/history.js": "window.H={v:1};\n",
    "valor/data/moves.js": "window.M={v:1};\n",
    "valor/data/ops.js": "window.O={v:1};\n",
}


@pytest.fixture
def repos(tmp_path):
    """bare (o 'GitHub') + clone `ci` (a rodada da Mesa) + clone `feeder` (o Windows)."""
    env = _env(tmp_path)
    (tmp_path / "home").mkdir()
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(bare), env=env)
    ci = tmp_path / "ci"
    git(tmp_path, "clone", "-q", str(bare), str(ci), env=env)
    for rel, txt in ARQUIVOS_INICIAIS.items():
        escreve(ci, rel, txt)
    git(ci, "add", "-A", env=env)
    git(ci, "commit", "-q", "-m", "base", env=env)
    git(ci, "push", "-q", "origin", "HEAD:main", env=env)
    git(ci, "fetch", "-q", "origin", "main", env=env)   # origin/main = base (como o checkout do runner)
    feeder = tmp_path / "feeder"
    git(tmp_path, "clone", "-q", str(bare), str(feeder), env=env)
    return {"tmp": tmp_path, "env": env, "bare": bare, "ci": ci, "feeder": feeder,
            "base": git(ci, "rev-parse", "HEAD", env=env).stdout.strip()}


def feeder_empurra(r, tag="ccc", n=95):
    """Ciclo real do feeder: pointer novo, snapshot velho SAI (rotação), novo entra,
    status muda; commit só nos add_paths dele; push. Devolve o sha."""
    f, env = r["feeder"], r["env"]
    git(f, "fetch", "-q", "origin", "main", env=env)
    git(f, "reset", "-q", "--hard", "origin/main", env=env)
    snaps = Path(f) / "data/odds/_snapshots"
    for velho in snaps.glob("pinnacle_full_*.jsonl"):
        velho.unlink()
    escreve(f, f"data/odds/_snapshots/pinnacle_full_{tag}.jsonl", f"pinn tick {tag}\n")
    escreve(f, "data/odds/pinnacle_latest_full.json",
            f'{{"file":"pinnacle_full_{tag}.jsonl","n":{n},"captured_by":"local"}}\n')
    escreve(f, "data/odds/pinnacle_latest.json", f'{{"file":"pinnacle_full_{tag}.jsonl"}}\n')
    escreve(f, "data/odds/_status/pinnacle.json", f'{{"n_events":{n},"origem":"feeder-{tag}"}}\n')
    git(f, "add", "data/odds/pinnacle_latest_full.json", "data/odds/pinnacle_latest.json",
        "data/odds/_snapshots/pinnacle_full_*.jsonl", "data/odds/_status/pinnacle.json", env=env)
    git(f, "commit", "-q", "-m", f"pinnacle: feed local ({n} eventos) [skip ci]", env=env)
    git(f, "push", "-q", "origin", "HEAD:main", env=env)
    return git(f, "rev-parse", "HEAD", env=env).stdout.strip()


def rodada_local_produz(ci):
    """O que a rodada da Mesa deixa na árvore ANTES do persist (não commitado):
    ticks/keys novos, js novos, status das outras casas — e também o status da
    Pinnacle capturado pela NUVEM (degradado), que o feeder tem que vencer."""
    escreve(ci, "data/odds_history/ticks/2026-09-05.jsonl", '{"tick":1}\n{"tick":2}\n')
    escreve(ci, "data/odds_history/keys/2026-09.p001.json", '{"k":2}\n')
    escreve(ci, "valor/data/history.js", "window.H={v:2};\n")
    escreve(ci, "data/odds/_status/betano.json", '{"n_events":12}\n')
    escreve(ci, "data/odds/_status/summary.json", '{"ts_brt":"2026-09-05 11:00"}\n')
    escreve(ci, "data/odds/_status/pinnacle.json", '{"n_events":6,"origem":"nuvem-degradada"}\n')


# ───────────────────────── FEEDER_PATHS: coerência com o feeder real ─────────────────────────

def _amostra(glob):
    return glob.replace("*", "X0f9")


def test_feeder_paths_cobrem_os_arquivos_reais_e_nada_do_persist():
    do_feeder = [
        "data/odds/pinnacle_latest.json", "data/odds/pinnacle_latest_full.json",
        "data/odds/superbet_latest.json", "data/odds/superbet_latest_full.json",
        "data/odds/_snapshots/pinnacle_full_76efe038c427327b29ca.jsonl",
        "data/odds/_snapshots/superbet_full_ee4ce0e00247491f3202.jsonl",
        "data/odds/_status/pinnacle.json", "data/odds/_status/superbet.json",
        "data/odds/_status/superbet_diag.json",
    ]
    do_persist = [
        "data/odds/_status/summary.json", "data/odds/_status/bet365.json",
        "data/odds/_status/betano.json", "data/odds/_status/history.jsonl",
        "data/odds/_snapshots/betano_full_1eb4c183b60c7cf12991.jsonl",
        "data/odds/_snapshots/sofa_ab5e724e5e89c100a745.json",
        "data/odds/bet365_latest_full.json", "data/fixtures/sofa_latest.json",
        "data/odds_history/ticks/2026-09-05.jsonl", "data/odds_history/keys/2026-09.p002.json",
        "data/odds_history/ledger/2026-09.jsonl", "data/odds_history/results/settlement_status.json",
        "valor/data/history.js", "valor/data/board.js", "persist_snapshot.sh",
        # '*' não pode atravessar '/'
        "data/odds/pinnacle_latest/x.json", "data/odds/_snapshots/pinnacle_full_a/b.jsonl",
    ]
    assert all(pr.eh_caminho_do_feeder(p) for p in do_feeder), pr.fora_do_feeder(do_feeder)
    assert pr.fora_do_feeder(do_persist) == do_persist


def test_feeder_paths_coerentes_com_feeder_mesa_once():
    """Lê os add_paths REAIS (4º argumento de cada ciclo_casa) sem importar o feeder."""
    arvore = ast.parse((RAIZ / "feeder_mesa_once.py").read_text(encoding="utf-8"))
    globs = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call) and getattr(no.func, "id", None) == "ciclo_casa":
            lista = no.args[3]
            assert isinstance(lista, ast.List)
            globs += [e.value for e in lista.elts]
    assert len(globs) >= 7, globs
    nao_cobertos = [g for g in globs if not pr.eh_caminho_do_feeder(_amostra(g))]
    assert not nao_cobertos, f"feeder_mesa_once.py adiciona caminho fora de FEEDER_PATHS: {nao_cobertos}"


def test_feeder_paths_coerentes_com_pinnacle_feeder_local_sh():
    sh = (RAIZ / "pinnacle_feeder_local.sh").read_text(encoding="utf-8")
    globs = []
    for linha in re.findall(r"^\s*git add (.+?)(?: 2>>.*)?$", sh, flags=re.M):
        globs += linha.split()
    assert len(globs) >= 7, globs
    nao_cobertos = [g for g in globs if not pr.eh_caminho_do_feeder(_amostra(g))]
    assert not nao_cobertos, f"pinnacle_feeder_local.sh adiciona caminho fora de FEEDER_PATHS: {nao_cobertos}"


# ───────────────────────── classificação ─────────────────────────

def test_classifica_feeder_quando_so_o_feeder_avancou(repos):
    r = repos
    feeder_empurra(r)
    git(r["ci"], "fetch", "-q", "origin", "main", env=r["env"])
    classe, arquivos = pr.classifica_avanco("HEAD", "origin/main", cwd=str(r["ci"]))
    assert classe == "feeder"
    assert arquivos == [
        "data/odds/_snapshots/pinnacle_full_aaa.jsonl",   # D — rename NÃO pode esconder isto
        "data/odds/_snapshots/pinnacle_full_ccc.jsonl",   # A
        "data/odds/_status/pinnacle.json",
        "data/odds/pinnacle_latest.json",
        "data/odds/pinnacle_latest_full.json",
    ]


def test_classifica_outro_quando_outra_rodada_tocou_ticks(repos):
    r = repos
    feeder_empurra(r)
    outra = r["tmp"] / "outra"
    git(r["tmp"], "clone", "-q", str(r["bare"]), str(outra), env=r["env"])
    escreve(outra, "data/odds_history/ticks/2026-09-05.jsonl", '{"tick":1}\n{"tick":9}\n')
    git(outra, "commit", "-q", "-am", "odds: snapshot [close] [skip ci]", env=r["env"])
    git(outra, "push", "-q", "origin", "HEAD:main", env=r["env"])
    git(r["ci"], "fetch", "-q", "origin", "main", env=r["env"])
    classe, arquivos = pr.classifica_avanco("HEAD", "origin/main", cwd=str(r["ci"]))
    assert classe == "outro"
    assert pr.fora_do_feeder(arquivos) == ["data/odds_history/ticks/2026-09-05.jsonl"]
    with pytest.raises(pr.AvancoNaoEhDoFeeder):
        pr.aplica_avanco_feeder("HEAD", "origin/main", cwd=str(r["ci"]))
    assert git(r["ci"], "rev-parse", "HEAD", env=r["env"]).stdout.strip() == r["base"]   # nada tocado


def test_classifica_outro_quando_main_foi_reescrito(repos):
    """Histórico reescrito (force-push): mesmo o diff sendo só do feeder, é 'outro'."""
    r = repos
    f = r["feeder"]
    git(f, "reset", "-q", "--hard", "HEAD~0", env=r["env"])
    git(f, "commit", "-q", "--allow-empty", "--amend", "-m", "base reescrita", env=r["env"])
    escreve(f, "data/odds/_status/pinnacle.json", '{"n_events":1}\n')
    git(f, "commit", "-q", "-am", "pinnacle: feed local", env=r["env"])
    git(f, "push", "-q", "--force", "origin", "HEAD:main", env=r["env"])
    git(r["ci"], "fetch", "-q", "origin", "main", env=r["env"])
    classe, arquivos = pr.classifica_avanco("HEAD", "origin/main", cwd=str(r["ci"]))
    assert classe == "outro"
    assert not pr.eh_ancestral("HEAD", "origin/main", cwd=str(r["ci"]))
    with pytest.raises(pr.AvancoNaoEhDoFeeder):
        pr.aplica_avanco_feeder("HEAD", "origin/main", cwd=str(r["ci"]))


# ───────────────────────── aplicação barata ─────────────────────────

def _confere_arvore_reconciliada(root, sha_feeder_status="feeder-ccc"):
    root = Path(root)
    assert not (root / "data/odds/_snapshots/pinnacle_full_aaa.jsonl").exists()   # rotacionado
    assert (root / "data/odds/_snapshots/pinnacle_full_ccc.jsonl").read_text() == "pinn tick ccc\n"
    assert "pinnacle_full_ccc" in (root / "data/odds/pinnacle_latest_full.json").read_text()
    assert sha_feeder_status in (root / "data/odds/_status/pinnacle.json").read_text()  # feeder vence a nuvem
    # o que a rodada produziu fica
    assert (root / "data/odds_history/ticks/2026-09-05.jsonl").read_text() == '{"tick":1}\n{"tick":2}\n'
    assert (root / "valor/data/history.js").read_text() == "window.H={v:2};\n"
    assert (root / "data/odds/_status/betano.json").read_text() == '{"n_events":12}\n'
    assert (root / "data/odds/_snapshots/betano_full_bbb.jsonl").exists()


def test_aplica_traz_o_feeder_e_mantem_o_local(repos):
    r = repos
    sha_feeder = feeder_empurra(r)
    rodada_local_produz(r["ci"])
    git(r["ci"], "fetch", "-q", "origin", "main", env=r["env"])
    res = pr.aplica_avanco_feeder("HEAD", "origin/main", cwd=str(r["ci"]))
    assert (res["trazidos"], res["apagados"]) == (4, 1)
    assert git(r["ci"], "rev-parse", "HEAD", env=r["env"]).stdout.strip() == sha_feeder
    _confere_arvore_reconciliada(r["ci"])
    # index coerente com a árvore: o snapshot rotacionado saiu do index também
    idx = git(r["ci"], "ls-files", "data/odds/_snapshots", env=r["env"]).stdout.split()
    assert "data/odds/_snapshots/pinnacle_full_aaa.jsonl" not in idx
    assert "data/odds/_snapshots/pinnacle_full_ccc.jsonl" in idx
    # o diff contra o main novo é SÓ o que a rodada produziu (nada do feeder revertido)
    git(r["ci"], "add", "-A", "data/odds/_status", "data/odds_history", "valor/data", env=r["env"])
    staged = git(r["ci"], "diff", "--cached", "--name-only", env=r["env"]).stdout.split()
    assert set(staged) == {"data/odds/_status/betano.json", "data/odds/_status/summary.json",
                           "data/odds_history/keys/2026-09.p001.json",
                           "data/odds_history/ticks/2026-09-05.jsonl", "valor/data/history.js"}


def test_aplica_com_index_ja_staged_o_retry_do_persist(repos):
    """Após um push rejeitado o persist faz `reset --soft HEAD~1`: o index fica com as
    mudanças da rodada JÁ staged. A 2ª volta tem que reconciliar em cima disso."""
    r = repos
    rodada_local_produz(r["ci"])
    git(r["ci"], "add", "-A", "data/odds/_status", "data/odds_history", "valor/data", env=r["env"])
    git(r["ci"], "commit", "-q", "-m", "odds: snapshot (vai ser rejeitado)", env=r["env"])
    feeder_empurra(r)                                     # o feeder ganhou a corrida
    assert git(r["ci"], "push", "-q", "origin", "HEAD:main", env=r["env"], check=False).returncode != 0
    git(r["ci"], "reset", "-q", "--soft", "HEAD~1", env=r["env"])   # exatamente o que o .sh faz
    git(r["ci"], "fetch", "-q", "origin", "main", env=r["env"])
    assert pr.classifica_avanco("HEAD", "origin/main", cwd=str(r["ci"]))[0] == "feeder"
    pr.aplica_avanco_feeder("HEAD", "origin/main", cwd=str(r["ci"]))
    _confere_arvore_reconciliada(r["ci"])
    git(r["ci"], "commit", "-q", "-m", "odds: snapshot (2ª volta)", env=r["env"])
    git(r["ci"], "push", "-q", "origin", "HEAD:main", env=r["env"])
    assert le_no_main(r["bare"], "data/odds_history/ticks/2026-09-05.jsonl", r["env"]) == '{"tick":1}\n{"tick":2}\n'
    assert le_no_main(r["bare"], "data/odds/_snapshots/pinnacle_full_aaa.jsonl", r["env"]) is None
    assert "feeder-ccc" in le_no_main(r["bare"], "data/odds/_status/pinnacle.json", r["env"])


# ───────────────────────── persist_snapshot.sh REAL, ponta a ponta ─────────────────────────

STUBS_PIPELINE = ["migrate_history_keys.py", "history_ingest.py", "history_close.py",
                  "history_settle.py", "build_model_ledger.py", "build_history.py",
                  "build_moves.py", "build_openclose.py", "build_ops.py",
                  "build_board.py", "build_manifest.py"]


def prepara_persist(r, corrida_no_push=False):
    """Shims no PATH: `python` (o runner tem, o Mac não) e, se pedido, um `git` que
    faz o feeder empurrar DE NOVO no 1º `git push` — a corrida real entre o fetch e
    o push. Stubs do pipeline no cwd registram 'reingest chamado' (caso B)."""
    ci, env, tmp = r["ci"], dict(r["env"]), r["tmp"]
    binx = tmp / "bin"
    binx.mkdir(exist_ok=True)
    real_git = subprocess.run(["which", "git"], capture_output=True, text=True,
                              env=env).stdout.strip()
    (binx / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    if corrida_no_push:
        marcador = tmp / "_corrida_feita"
        # o mesmo ciclo do pinnacle_feeder_local.sh, em shell, disparado pelo shim no
        # 1º `git push` do persist (o push real é então rejeitado de verdade, non-fast-forward)
        feeder_de_novo = tmp / "feeder_de_novo.sh"
        feeder_de_novo.write_text(textwrap.dedent(f"""
            #!/bin/sh
            set -e
            cd "{r['feeder']}"
            G="{real_git}"
            $G fetch -q origin main && $G reset -q --hard origin/main
            rm -f data/odds/_snapshots/pinnacle_full_*.jsonl
            printf 'pinn tick ddd\\n' > data/odds/_snapshots/pinnacle_full_ddd.jsonl
            printf '{{"file":"pinnacle_full_ddd.jsonl","n":97,"captured_by":"local"}}\\n' > data/odds/pinnacle_latest_full.json
            printf '{{"file":"pinnacle_full_ddd.jsonl"}}\\n' > data/odds/pinnacle_latest.json
            printf '{{"n_events":97,"origem":"feeder-ddd"}}\\n' > data/odds/_status/pinnacle.json
            # glob ENTRE ASPAS: o git faz o pathspec (como o feeder_mesa_once.py, que passa
            # o glob literal) e registra o D do snapshot rotacionado; expandido pelo shell
            # só o arquivo novo entraria e a remoção nunca chegaria ao main
            $G add 'data/odds/pinnacle_latest*.json' 'data/odds/_snapshots/pinnacle_full_*.jsonl' data/odds/_status/pinnacle.json
            $G commit -q -m "pinnacle: feed local (97 eventos) [skip ci]"
            $G push -q origin HEAD:main
        """))
        (binx / "git").write_text(textwrap.dedent(f"""
            #!/bin/sh
            if [ "$1" = "push" ] && [ ! -f "{marcador}" ]; then
              touch "{marcador}"
              sh "{feeder_de_novo}" >/dev/null 2>&1 || echo "feeder de novo FALHOU" >&2
            fi
            exec "{real_git}" "$@"
        """))
    for f in binx.iterdir():
        f.chmod(0o755)
    env["PATH"] = f"{binx}:{env['PATH']}"
    for s in STUBS_PIPELINE:
        corpo = "open('_reingest.log','a').write('reingest chamado: %s\\n')\n" % s
        if s == "history_ingest.py":   # o pipeline de verdade regenera os ticks sobre a base nova
            corpo += "open('data/odds_history/ticks/2026-09-05.jsonl','a').write('{\"tick\":\"reingest\"}\\n')\n"
        (ci / s).write_text(corpo)
    return env


def roda_persist(r, env, mode="close", gate="skipped"):
    env = dict(env, MODE=mode, GATE_OUTCOME=gate)
    p = subprocess.run(["bash", str(PERSIST_SH)], cwd=str(r["ci"]), capture_output=True,
                       text=True, env=env, timeout=120)
    return p.returncode, p.stdout + p.stderr


def test_persist_sh_caso_A_reconcilia_barato_sem_reingest(repos):
    r = repos
    env = prepara_persist(r)
    sha_feeder = feeder_empurra(r)        # o feeder avançou o main DURANTE a rodada
    rodada_local_produz(r["ci"])
    rc, log = roda_persist(r, env)
    assert rc == 0, log
    assert "avanço só do feeder: 5 arquivos (4 trazidos de origin/main, 1 apagados), reconciliado em" in log, log
    assert "sem re-rodar o pipeline" in log
    assert "reconciliando o histórico sobre a base nova" not in log
    assert not (r["ci"] / "_reingest.log").exists()
    assert "Snapshot persistido e enviado (tentativa 1)" in log
    bare, e = r["bare"], r["env"]
    # main final = ticks/js desta rodada + arquivos do feeder, com o commit do feeder na história
    assert le_no_main(bare, "data/odds_history/ticks/2026-09-05.jsonl", e) == '{"tick":1}\n{"tick":2}\n'
    assert le_no_main(bare, "valor/data/history.js", e) == "window.H={v:2};\n"
    assert le_no_main(bare, "data/odds/_status/betano.json", e) == '{"n_events":12}\n'
    assert "feeder-ccc" in le_no_main(bare, "data/odds/_status/pinnacle.json", e)
    assert "pinnacle_full_ccc" in le_no_main(bare, "data/odds/pinnacle_latest_full.json", e)
    assert le_no_main(bare, "data/odds/_snapshots/pinnacle_full_ccc.jsonl", e) == "pinn tick ccc\n"
    assert le_no_main(bare, "data/odds/_snapshots/pinnacle_full_aaa.jsonl", e) is None
    git(bare, "merge-base", "--is-ancestor", sha_feeder, "main", env=e)
    assert git(bare, "rev-list", "--count", "main", env=e).stdout.strip() == "3"   # base, feeder, snapshot


def test_persist_sh_caso_A_feeder_empurra_de_novo_entre_fetch_e_push(repos):
    r = repos
    env = prepara_persist(r, corrida_no_push=True)
    feeder_empurra(r)                     # 1º avanço: antes do persist
    rodada_local_produz(r["ci"])
    rc, log = roda_persist(r, env)        # 2º avanço: o shim faz o feeder empurrar no 1º push
    assert rc == 0, log
    assert "push rejeitado (tentativa 1)" in log, log
    assert log.count("avanço só do feeder:") == 2, log
    assert "Snapshot persistido e enviado (tentativa 2)" in log
    assert not (r["ci"] / "_reingest.log").exists()
    bare, e = r["bare"], r["env"]
    assert le_no_main(bare, "data/odds_history/ticks/2026-09-05.jsonl", e) == '{"tick":1}\n{"tick":2}\n'
    assert "feeder-ddd" in le_no_main(bare, "data/odds/_status/pinnacle.json", e)
    assert le_no_main(bare, "data/odds/_snapshots/pinnacle_full_ddd.jsonl", e) == "pinn tick ddd\n"
    assert le_no_main(bare, "data/odds/_snapshots/pinnacle_full_ccc.jsonl", e) is None
    assert le_no_main(bare, "data/odds/_snapshots/pinnacle_full_aaa.jsonl", e) is None
    assert git(bare, "rev-list", "--count", "main", env=e).stdout.strip() == "4"   # base, f1, f2, snapshot


def test_persist_sh_caso_B_cai_no_reingest_completo(repos):
    r = repos
    env = prepara_persist(r)
    outra = r["tmp"] / "outra"
    git(r["tmp"], "clone", "-q", str(r["bare"]), str(outra), env=r["env"])
    escreve(outra, "data/odds_history/ticks/2026-09-05.jsonl", '{"tick":1}\n{"tick":9}\n')
    git(outra, "commit", "-q", "-am", "odds: snapshot [close] [skip ci]", env=r["env"])
    git(outra, "push", "-q", "origin", "HEAD:main", env=r["env"])
    rodada_local_produz(r["ci"])
    rc, log = roda_persist(r, env)
    assert rc == 0, log
    assert "→ outro" in log and "! data/odds_history/ticks/2026-09-05.jsonl" in log, log
    assert "reconciliando o histórico sobre a base nova" in log
    assert "avanço só do feeder" not in log
    chamados = (r["ci"] / "_reingest.log").read_text()
    assert "reingest chamado: history_ingest.py" in chamados
    assert "reingest chamado: build_board.py" not in chamados     # close: sem board
    # o ticks do main é o da base nova + o que o (stub do) pipeline regenerou — nada revertido
    assert le_no_main(r["bare"], "data/odds_history/ticks/2026-09-05.jsonl", r["env"]) == \
        '{"tick":1}\n{"tick":9}\n{"tick":"reingest"}\n'


def test_persist_sh_caso_A_full_pointer_do_feeder_vence_o_da_nuvem(repos):
    """No full o stage() inclui *_latest_full.json e _snapshots inteiros — os MESMOS
    caminhos do feeder. A captura da nuvem (degradada) não pode voltar por cima do
    pointer do feeder, e os snapshots das outras casas desta rodada têm que entrar."""
    r = repos
    env = prepara_persist(r)
    for rel, txt in {"valor/data/board.js": "window.B={v:1};\n",
                     "valor/data/manifest.js": "window.MF={v:1};\n",
                     "data/odds/betano_latest_full.json": '{"file":"betano_full_bbb.jsonl"}\n'}.items():
        escreve(r["ci"], rel, txt)
    git(r["ci"], "add", "-A", env=r["env"])
    git(r["ci"], "commit", "-q", "-m", "base full", env=r["env"])
    git(r["ci"], "push", "-q", "origin", "HEAD:main", env=r["env"])
    git(r["ci"], "fetch", "-q", "origin", "main", env=r["env"])
    feeder_empurra(r)
    rodada_local_produz(r["ci"])
    # a nuvem capturou a Pinnacle degradada (6 eventos) e a Betano nova
    escreve(r["ci"], "data/odds/pinnacle_latest_full.json", '{"file":"pinnacle_full_nuvem.jsonl","n":6,"captured_by":"actions"}\n')
    escreve(r["ci"], "data/odds/_snapshots/pinnacle_full_nuvem.jsonl", "pinn nuvem\n")
    escreve(r["ci"], "data/odds/betano_latest_full.json", '{"file":"betano_full_nova.jsonl"}\n')
    escreve(r["ci"], "data/odds/_snapshots/betano_full_nova.jsonl", "betano tick 2\n")
    escreve(r["ci"], "valor/data/board.js", "window.B={v:2};\n")
    rc, log = roda_persist(r, env, mode="full", gate="success")
    assert rc == 0, log
    assert "avanço só do feeder: 5 arquivos" in log and not (r["ci"] / "_reingest.log").exists()
    bare, e = r["bare"], r["env"]
    assert "pinnacle_full_ccc" in le_no_main(bare, "data/odds/pinnacle_latest_full.json", e)   # feeder venceu
    assert le_no_main(bare, "data/odds/_snapshots/pinnacle_full_aaa.jsonl", e) is None
    assert "betano_full_nova" in le_no_main(bare, "data/odds/betano_latest_full.json", e)      # nuvem entrou
    assert le_no_main(bare, "data/odds/_snapshots/betano_full_nova.jsonl", e) == "betano tick 2\n"
    assert le_no_main(bare, "valor/data/board.js", e) == "window.B={v:2};\n"
    assert le_no_main(bare, "data/odds_history/ticks/2026-09-05.jsonl", e) == '{"tick":1}\n{"tick":2}\n'


def test_persist_sh_sem_avanco_e_sem_artefatos_sai_limpo(repos):
    r = repos
    env = prepara_persist(r)
    rc, log = roda_persist(r, env)
    assert rc == 0 and "Sem artefatos novos" in log, log
    assert not (r["ci"] / "_reingest.log").exists()
