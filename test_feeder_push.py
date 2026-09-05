# -*- coding: utf-8 -*-
"""test_feeder_push.py — auditoria de 05/09/2026: push rejeitado do feeder
NÃO pode reverter o que origin/main ganhou no meio do ciclo.

Reproduz a corrida com repositórios git TEMPORÁRIOS (bare + 2 clones):
  A = feeder (commita pinnacle_*), B = Mesa (commita ticks/ + valor/data/).
  B empurra ANTES do A → o push do A é rejeitado.

  - função ANTIGA (cópia literal aqui): `reset --soft origin/main` + commit
    re-commita a árvore INTEIRA do ciclo → o main perde ticks/x.jsonl e
    board.js (4 casos reais: 6939350, c308815, 54371aa, 864e629).
  - função NOVA: main termina com ticks/x.jsonl E board.js E os arquivos do
    feeder; o commit reconciliado toca SÓ os add_paths (inclusive a deleção
    do snapshot antigo que a rotação de 2 gerações faz todo ciclo).
  - sem rejeição: comportamento idêntico ao de antes (mesmo sha, 1 push).
  - rejeição dupla (main avança de novo entre o fetch e o 2º push).
  - rejeições sem fim: esgota PUSH_RETRIES, devolve False e o main fica intacto.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import feeder_mesa_once as feeder  # noqa: E402

ADD_PATHS_PINNACLE = [
    "data/odds/pinnacle_latest*.json",
    "data/odds/_snapshots/pinnacle_full_*.jsonl",
    "data/odds/_status/pinnacle.json",
]
SNAP_VELHO = "data/odds/_snapshots/pinnacle_full_aaaa.jsonl"
SNAP_NOVO = "data/odds/_snapshots/pinnacle_full_bbbb.jsonl"
LATEST = "data/odds/pinnacle_latest.json"
STATUS = "data/odds/_status/pinnacle.json"


# ---------------------------------------------------------------- helpers git
def sh(repo, *args):
    p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def sh_ok(repo, *args):
    rc, out = sh(repo, *args)
    assert rc == 0, f"git {' '.join(args)} falhou em {repo}: {out}"
    return out


def escreve(repo, rel, texto):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")


def commit_tudo(repo, msg):
    sh_ok(repo, "add", "-A")
    sh_ok(repo, "commit", "-q", "-m", msg)
    return sh_ok(repo, "rev-parse", "HEAD").strip()


def arvore_main(bare):
    """Conjunto de caminhos versionados em refs/heads/main do bare."""
    return set(sh_ok(bare, "ls-tree", "-r", "--name-only", "main").split())


def conteudo_main(bare, rel):
    return sh_ok(bare, "show", f"main:{rel}")


def arquivos_do_commit(repo, rev="HEAD"):
    out = sh_ok(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", f"{rev}~1", rev)
    return set(out.split())


def dentro_dos_add_paths(paths, add_paths):
    return all(any(fnmatch.fnmatch(p, pat) for pat in add_paths) for p in paths)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """bare `origin` (branch main) + clone A (feeder) + clone B (Mesa), com o
    módulo do feeder apontado pro clone A (ROOT/LOG são globais lidos na hora)."""
    bare = tmp_path / "origin.git"
    sh_ok(tmp_path, "init", "-q", "--bare", str(bare))
    sh_ok(bare, "symbolic-ref", "HEAD", "refs/heads/main")

    seed = tmp_path / "seed"
    sh_ok(tmp_path, "clone", "-q", str(bare), str(seed))
    _config(seed)
    sh_ok(seed, "checkout", "-q", "-b", "main")
    escreve(seed, LATEST, '{"v":0}')
    escreve(seed, STATUS, '{"n_events":0}')
    escreve(seed, SNAP_VELHO, '{"odd":1}\n')
    escreve(seed, "data/odds/_status/superbet.json", '{"n_events":5}')
    # existe no repo real; `git add` com pathspec literal SEM arquivo dá rc 128
    escreve(seed, "data/odds/_status/superbet_diag.json", "{}")
    escreve(seed, "README.md", "seed\n")
    commit_tudo(seed, "seed")
    sh_ok(seed, "push", "-q", "origin", "main")

    a = tmp_path / "A_feeder"
    b = tmp_path / "B_mesa"
    sh_ok(tmp_path, "clone", "-q", str(bare), str(a))
    sh_ok(tmp_path, "clone", "-q", str(bare), str(b))
    _config(a)
    _config(b)

    monkeypatch.setattr(feeder, "ROOT", str(a))
    monkeypatch.setattr(feeder, "LOG", str(tmp_path / "feeder.log"))
    return {"bare": bare, "a": a, "b": b}


def _config(repo):
    sh_ok(repo, "config", "user.email", "teste@rdu.local")
    sh_ok(repo, "config", "user.name", "teste")
    sh_ok(repo, "config", "commit.gpgsign", "false")


def ciclo_feeder_local(a):
    """Simula o que ciclo_casa faz antes do push: fetch escreveu os arquivos da
    Pinnacle (latest + status + snapshot novo, rotação apagou o antigo) e
    `git add <add_paths>` + commit."""
    escreve(a, LATEST, '{"v":1}')
    escreve(a, STATUS, '{"n_events":77}')
    escreve(a, SNAP_NOVO, '{"odd":2}\n')
    (Path(a) / SNAP_VELHO).unlink()
    sh_ok(a, "add", *ADD_PATHS_PINNACLE)
    sh_ok(a, "commit", "-q", "-m", "pinnacle: feed local (77 eventos) [skip ci]")
    return sh_ok(a, "rev-parse", "HEAD").strip()


def mesa_avanca_main(b, n=1):
    """A Mesa (clone B) commita snapshot em caminhos DISJUNTOS do feeder e
    empurra — é o que acontece entre o sync e o push do feeder."""
    sh_ok(b, "pull", "-q", "--rebase", "origin", "main")
    escreve(b, f"ticks/x{n}.jsonl", '{"tick":%d}\n' % n)
    escreve(b, "valor/data/board.js", f"window.BOARD={{n:{n}}};\n")
    sha = commit_tudo(b, f"mesa: snapshot {n}")
    sh_ok(b, "push", "-q", "origin", "main")
    return sha


# ------------------------------------------------ a função ANTIGA (cópia literal)
def push_verificado_antigo(commit_msg):
    """Cópia da push_verificado que rodou até 05/09/2026 — documenta o defeito."""
    git = feeder.git
    rc, _ = git("push", "-q", "origin", "HEAD:main")
    if rc != 0:
        git("fetch", "-q", "origin", "main", timeout=feeder.GIT_TIMEOUT)
        git("reset", "--soft", "origin/main")
        git("commit", "-q", "-m", commit_msg)
        rc, _ = git("push", "-q", "origin", "HEAD:main")
    git("fetch", "-q", "origin", "main", timeout=feeder.GIT_TIMEOUT)
    rc_v, _ = git("merge-base", "--is-ancestor", "HEAD", "origin/main")
    return rc_v == 0


# ------------------------------------------------------------------- testes
def test_antiga_reverte_o_que_o_main_ganhou(repos):
    """O defeito, reproduzido: função antiga 'confirma' o push e o main PERDE
    ticks/x1.jsonl + board.js que a Mesa tinha acabado de empurrar."""
    a, b, bare = repos["a"], repos["b"], repos["bare"]
    ciclo_feeder_local(a)
    mesa_avanca_main(b, 1)

    assert push_verificado_antigo("pinnacle: feed local [skip ci]") is True  # ela achava que deu certo
    tree = arvore_main(bare)
    assert "ticks/x1.jsonl" not in tree          # ← o dano
    assert "valor/data/board.js" not in tree     # ← o dano
    assert SNAP_NOVO in tree
    # e o commit do feeder "toca" caminhos que não são dele (as deleções da Mesa)
    tocados = arquivos_do_commit(a)
    assert {"ticks/x1.jsonl", "valor/data/board.js"} <= tocados


def test_nova_preserva_main_e_toca_so_os_add_paths(repos):
    a, b, bare = repos["a"], repos["b"], repos["bare"]
    old = ciclo_feeder_local(a)
    sha_mesa = mesa_avanca_main(b, 1)

    assert feeder.push_verificado("pinnacle: feed local [skip ci]", ADD_PATHS_PINNACLE) is True

    tree = arvore_main(bare)
    # o que a Mesa empurrou continua lá
    assert "ticks/x1.jsonl" in tree
    assert "valor/data/board.js" in tree
    assert conteudo_main(bare, "valor/data/board.js") == "window.BOARD={n:1};\n"
    # o que o feeder fez chegou: novo snapshot, latest/status novos, snapshot velho apagado
    assert SNAP_NOVO in tree
    assert SNAP_VELHO not in tree
    assert conteudo_main(bare, LATEST) == '{"v":1}'
    assert conteudo_main(bare, STATUS) == '{"n_events":77}'
    # caminho do feeder que o ciclo NÃO mexeu (superbet) fica como o main tem
    assert conteudo_main(bare, "data/odds/_status/superbet.json") == '{"n_events":5}'
    # o commit reconciliado é filho direto do commit da Mesa e toca só add_paths
    head = sh_ok(a, "rev-parse", "HEAD").strip()
    assert head != old
    assert sh_ok(a, "rev-parse", "HEAD~1").strip() == sha_mesa
    assert sh_ok(bare, "rev-parse", "main").strip() == head
    tocados = arquivos_do_commit(a)
    assert tocados == {LATEST, STATUS, SNAP_NOVO, SNAP_VELHO}
    assert dentro_dos_add_paths(tocados, ADD_PATHS_PINNACLE)
    # working tree limpa (nada preso pro próximo ciclo)
    assert sh_ok(a, "status", "--porcelain").strip() == ""


def test_nova_sem_rejeicao_e_identica_ao_antigo(repos):
    """Sem corrida: 1 push, mesmo sha, nenhuma reconciliação."""
    a, bare = repos["a"], repos["bare"]
    old = ciclo_feeder_local(a)
    pushes = []
    real_git = feeder.git

    def espiao(*args, **kw):
        if args and args[0] == "push":
            pushes.append(args)
        assert args[0] not in ("reset", "checkout", "rm"), f"reconciliação indevida: {args}"
        return real_git(*args, **kw)

    feeder.git = espiao
    try:
        assert feeder.push_verificado("pinnacle: feed local [skip ci]", ADD_PATHS_PINNACLE) is True
    finally:
        feeder.git = real_git
    assert len(pushes) == 1
    assert sh_ok(a, "rev-parse", "HEAD").strip() == old
    assert sh_ok(bare, "rev-parse", "main").strip() == old


def test_nova_rejeicao_dupla(repos):
    """A Mesa empurra de novo entre o fetch e o 2º push: 3 pushes, tudo preservado."""
    a, b, bare = repos["a"], repos["b"], repos["bare"]
    ciclo_feeder_local(a)
    real_git = feeder.git
    n_push = {"n": 0}

    def com_corrida(*args, **kw):
        if args and args[0] == "push":
            n_push["n"] += 1
            if n_push["n"] <= 2:  # main avança logo ANTES do 1º e do 2º push
                mesa_avanca_main(b, n_push["n"])
        return real_git(*args, **kw)

    feeder.git = com_corrida
    try:
        assert feeder.push_verificado("pinnacle: feed local [skip ci]", ADD_PATHS_PINNACLE) is True
    finally:
        feeder.git = real_git

    assert n_push["n"] == 3
    tree = arvore_main(bare)
    assert {"ticks/x1.jsonl", "ticks/x2.jsonl", "valor/data/board.js", SNAP_NOVO, LATEST} <= tree
    assert SNAP_VELHO not in tree
    assert conteudo_main(bare, "valor/data/board.js") == "window.BOARD={n:2};\n"
    assert conteudo_main(bare, LATEST) == '{"v":1}'
    head = sh_ok(a, "rev-parse", "HEAD").strip()
    assert sh_ok(bare, "rev-parse", "main").strip() == head
    tocados = arquivos_do_commit(a)
    assert tocados == {LATEST, STATUS, SNAP_NOVO, SNAP_VELHO}
    assert dentro_dos_add_paths(tocados, ADD_PATHS_PINNACLE)


def test_nova_esgota_tentativas_sem_estragar_o_main(repos):
    """Main avançando antes de TODO push: devolve False (o chamador reseta) e
    o main fica exatamente com o que a Mesa empurrou."""
    a, b, bare = repos["a"], repos["b"], repos["bare"]
    ciclo_feeder_local(a)
    real_git = feeder.git
    n_push = {"n": 0}

    def sempre_corrida(*args, **kw):
        if args and args[0] == "push":
            n_push["n"] += 1
            mesa_avanca_main(b, n_push["n"])
        return real_git(*args, **kw)

    feeder.git = sempre_corrida
    try:
        assert feeder.push_verificado("pinnacle: feed local [skip ci]", ADD_PATHS_PINNACLE) is False
    finally:
        feeder.git = real_git

    assert n_push["n"] == 1 + feeder.PUSH_RETRIES
    ultimo_mesa = sh_ok(b, "rev-parse", "HEAD").strip()
    assert sh_ok(bare, "rev-parse", "main").strip() == ultimo_mesa
    tree = arvore_main(bare)
    assert {f"ticks/x{i}.jsonl" for i in range(1, n_push["n"] + 1)} <= tree
    assert SNAP_NOVO not in tree and SNAP_VELHO in tree  # feeder não entrou, main intacto


def test_nova_main_ja_tem_o_conteudo_do_feeder(repos):
    """Outro feeder (Mac) já empurrou byte a byte o mesmo ciclo: reconciliação
    dá 'nothing to commit' e o resultado é HEAD == origin/main (True)."""
    a, b, bare = repos["a"], repos["b"], repos["bare"]
    ciclo_feeder_local(a)
    # o clone B faz exatamente as mesmas mudanças e empurra antes
    escreve(b, LATEST, '{"v":1}')
    escreve(b, STATUS, '{"n_events":77}')
    escreve(b, SNAP_NOVO, '{"odd":2}\n')
    (Path(b) / SNAP_VELHO).unlink()
    commit_tudo(b, "pinnacle: feed do Mac")
    sh_ok(b, "push", "-q", "origin", "main")

    assert feeder.push_verificado("pinnacle: feed local [skip ci]", ADD_PATHS_PINNACLE) is True
    assert sh_ok(a, "rev-parse", "HEAD").strip() == sh_ok(bare, "rev-parse", "main").strip()
    assert sh_ok(a, "status", "--porcelain").strip() == ""


def test_nova_segunda_casa_do_mesmo_run(repos):
    """Sequência real do main(): pinnacle empurra OK, superbet commita EM CIMA
    (pai = commit da pinnacle, não a ponta do main) e é rejeitado. O delta é
    só o da superbet; pinnacle e Mesa ficam intactos no main."""
    a, b, bare = repos["a"], repos["b"], repos["bare"]
    ciclo_feeder_local(a)
    assert feeder.push_verificado("pinnacle: feed local [skip ci]", ADD_PATHS_PINNACLE) is True
    sha_pinn = sh_ok(a, "rev-parse", "HEAD").strip()

    add_superbet = ["data/odds/superbet_latest*.json",
                    "data/odds/_snapshots/superbet_full_*.jsonl",
                    "data/odds/_status/superbet.json",
                    "data/odds/_status/superbet_diag.json"]  # existe mas o ciclo não mexeu → fora do delta
    escreve(a, "data/odds/superbet_latest.json", '{"v":1}')
    escreve(a, "data/odds/_status/superbet.json", '{"n_events":336}')
    escreve(a, "data/odds/_snapshots/superbet_full_cccc.jsonl", "s\n")
    sh_ok(a, "add", *add_superbet)
    sh_ok(a, "commit", "-q", "-m", "superbet: feed local (336 eventos) [skip ci]")
    mesa_avanca_main(b, 7)

    assert feeder.push_verificado("superbet: feed local [skip ci]", add_superbet) is True
    tree = arvore_main(bare)
    assert {"ticks/x7.jsonl", "valor/data/board.js", SNAP_NOVO,
            "data/odds/superbet_latest.json", "data/odds/_snapshots/superbet_full_cccc.jsonl"} <= tree
    assert conteudo_main(bare, "data/odds/_status/superbet.json") == '{"n_events":336}'
    assert conteudo_main(bare, LATEST) == '{"v":1}'
    assert sh_ok(bare, "merge-base", "--is-ancestor", sha_pinn, "main") == ""
    tocados = arquivos_do_commit(a)
    assert tocados == {"data/odds/superbet_latest.json", "data/odds/_status/superbet.json",
                       "data/odds/_snapshots/superbet_full_cccc.jsonl"}
    assert dentro_dos_add_paths(tocados, add_superbet)


def test_delta_do_feeder_isola_add_paths(repos):
    """_delta_do_feeder lê A/M/D só dentro dos add_paths, ignorando o resto do
    commit — e não erra caminho que 'não existe em old' (casa sem arquivo novo)."""
    a = repos["a"]
    escreve(a, LATEST, '{"v":9}')
    escreve(a, SNAP_NOVO, "x\n")
    (Path(a) / SNAP_VELHO).unlink()
    escreve(a, "README.md", "fora dos add_paths\n")
    sha = commit_tudo(a, "misto")
    alterados, apagados = feeder._delta_do_feeder(sha, ADD_PATHS_PINNACLE + ["data/odds/_status/superbet_diag.json"])
    assert set(alterados) == {LATEST, SNAP_NOVO}
    assert apagados == [SNAP_VELHO]
    # commit inexistente → None (o chamador desiste, nunca reset --soft de árvore)
    assert feeder._delta_do_feeder("0" * 40, ADD_PATHS_PINNACLE) is None


def test_nova_delta_vazio_desiste(monkeypatch):
    """05/09 (revisão): delta vazio após push rejeitado = parse quebrado ou add_paths
    errado. Sem a guarda, seguia pra reset --hard + "nothing to commit" e devolvia True
    (CONFIRMADO) sem ter empurrado nada — fail-open. Agora desiste com False e NÃO
    toca no repo (nenhum reset/checkout/rm/commit)."""
    import feeder_mesa_once as feeder
    chamadas = []

    def fake_git(*a, **k):
        chamadas.append(a[0])
        if a[0] == "push":
            return 1, "! [rejected] HEAD -> main (fetch first)"
        if a[0] == "rev-parse":
            return 0, "0123456789abcdef\n"
        return 0, ""

    monkeypatch.setattr(feeder, "git", fake_git)
    monkeypatch.setattr(feeder, "log", lambda *a, **k: None)
    monkeypatch.setattr(feeder, "_delta_do_feeder", lambda commit, add_paths: ([], []))
    assert feeder.push_verificado("msg", ("data/odds/pinnacle_latest.json",)) is False
    assert not any(c in ("reset", "checkout", "rm", "commit") for c in chamadas)
    assert chamadas.count("push") == 1
