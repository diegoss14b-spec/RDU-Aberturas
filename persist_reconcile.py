# -*- coding: utf-8 -*-
"""Reconciliação BARATA do persist com o feeder local (05/09/2026).

Contexto: persist_snapshot.sh roda no runner do GitHub no fim de cada rodada da
Mesa. Quando origin/main avança durante a rodada, o script re-rodava o pipeline
INTEIRO (~15-18 min no runner) a cada avanço — e o feeder local da Pinnacle/
Superbet (feeder_mesa_once.py no Windows, pinnacle_feeder_local.sh no Mac)
avança o main a cada ~23 min tocando SÓ os caminhos dele. Auditoria de
05/09/2026: 11 de 48 runs morreram no teto de 75 min do job dentro do passo
"Commit snapshots + histórico".

Este módulo classifica o avanço (base local → origin/main) pelo `git diff
--name-only base head`:

  'feeder' — TODO arquivo tocado está em FEEDER_PATHS (os add_paths do feeder).
             Reconciliação barata (aplica_avanco_feeder): reset --soft pro main
             novo + checkout desses caminhos por cima da árvore local; arquivo
             apagado no main (rotação de snapshot) é apagado aqui. O que o
             persist produziu nesta rodada (ticks, keys, ledger, valor/data/*.js,
             snapshots das OUTRAS casas) fica intacto no index/árvore e vira o
             commit de sempre. Segundos, sem re-rodar nada.
  'outro'  — qualquer arquivo fora do conjunto (outra rodada da Mesa, feed de
             resultados, código) → o persist cai no reingest completo de antes.

Fail-closed: base que NÃO é ancestral do head (force-push, histórico reescrito)
é 'outro' mesmo que o diff pareça só do feeder — o reset --hard do reingest é o
único caminho seguro nesse caso.

FEEDER_PATHS é a fonte única, do lado do persist, dos caminhos do feeder. O
feeder NÃO é tocado (regra da entrega); o test_persist_reconcile.py lê os
add_paths REAIS do feeder_mesa_once.py e os `git add` do
pinnacle_feeder_local.sh e confere que esta lista os cobre — se alguém somar
um caminho no feeder sem somar aqui, o teste do CI grita (o efeito seria só o
persist voltar a fazer o reingest caro, nunca perder dado).

CLI (usado pelo persist_snapshot.sh; rc é o contrato, não o texto):
  python persist_reconcile.py classifica <base> <head>  → rc 0 feeder | 3 outro | 1 erro
  python persist_reconcile.py aplica     <base> <head>  → rc 0 ok | 3 recusou (outro) | 1 erro
"""
import os
import re
import subprocess
import sys

# Espelho dos add_paths do feeder_mesa_once.py (Windows) e do pinnacle_feeder_local.sh
# (Mac). `_status/{pinnacle,superbet}*.json` cobre pinnacle.json, superbet.json e
# superbet_diag.json (o `*` aqui NÃO atravessa '/', ver _glob_re).
FEEDER_PATHS = (
    "data/odds/pinnacle_latest*.json",
    "data/odds/superbet_latest*.json",
    "data/odds/_snapshots/pinnacle_full_*.jsonl",
    "data/odds/_snapshots/superbet_full_*.jsonl",
    "data/odds/_status/pinnacle*.json",
    "data/odds/_status/superbet*.json",
)

RC_FEEDER = 0
RC_ERRO = 1
RC_OUTRO = 3


class AvancoNaoEhDoFeeder(Exception):
    """aplica_avanco_feeder recusou: o avanço toca caminho fora de FEEDER_PATHS."""


def _glob_re(pattern):
    # fnmatch deixaria '*' atravessar '/' ('data/odds/pinnacle_latest*.json' casaria
    # 'data/odds/pinnacle_latest/qualquer/x.json'). Aqui '*' = [^/]* de propósito.
    partes = [re.escape(p) for p in pattern.split("*")]
    return re.compile("^" + "[^/]*".join(partes) + "$")


_FEEDER_RES = tuple(_glob_re(p) for p in FEEDER_PATHS)


def eh_caminho_do_feeder(path):
    """True se `path` (relativo à raiz do repo, com '/') casa algum FEEDER_PATHS."""
    path = str(path).replace(os.sep, "/")
    return any(r.match(path) for r in _FEEDER_RES)


def fora_do_feeder(paths):
    return [p for p in paths if not eh_caminho_do_feeder(p)]


def _git(args, cwd=None):
    p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace").strip()


def _sha(ref, cwd=None):
    rc, out, err = _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd)
    if rc != 0:
        raise RuntimeError(f"rev-parse {ref}: {err}")
    return out.decode().strip()


def eh_ancestral(base, head, cwd=None):
    rc, _, err = _git(["merge-base", "--is-ancestor", base, head], cwd)
    if rc == 0:
        return True
    if rc == 1:
        return False
    raise RuntimeError(f"merge-base --is-ancestor {base} {head}: {err}")


def arquivos_tocados(base, head, cwd=None):
    """Caminhos que diferem entre base e head (raiz do repo). --no-renames pra
    rename virar D+A: o snapshot velho apagado pelo feeder TEM que aparecer, senão
    ele sobrevive na árvore local e volta pro main."""
    rc, out, err = _git(["diff", "--name-only", "-z", "--no-renames", base, head], cwd)
    if rc != 0:
        raise RuntimeError(f"git diff {base} {head}: {err}")
    return sorted({p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p})


def classifica_avanco(base, head, cwd=None):
    """→ ('feeder' | 'outro', arquivos tocados entre base e head).

    'feeder' só quando base é ancestral de head E todo arquivo tocado está em
    FEEDER_PATHS. Zero arquivos tocados (base == head) conta como 'feeder' —
    não há nada a reconciliar; o chamador nem deveria chegar aqui nesse caso.
    """
    arquivos = arquivos_tocados(base, head, cwd)
    if not eh_ancestral(base, head, cwd):
        return "outro", arquivos
    if fora_do_feeder(arquivos):
        return "outro", arquivos
    return "feeder", arquivos


def aplica_avanco_feeder(base, head, cwd=None):
    """Reconciliação barata (caso A). Pré-condição re-checada aqui (fail-closed):
    classifica_avanco(base, head) == 'feeder', senão AvancoNaoEhDoFeeder e NADA é
    tocado.

    1) `git reset --soft <head>`: HEAD passa pro main novo; index e árvore ficam
       como estão (as mudanças desta rodada — staged ou não — sobrevivem, e é por
       isso que o retry após `reset --soft HEAD~1` do persist também funciona).
    2) caminho tocado que EXISTE em head → `git checkout <head> -- caminho`
       (index + árvore recebem a versão do feeder, por cima da local);
       caminho tocado que NÃO existe em head (snapshot rotacionado) → sai do
       index e da árvore. Sem isto o persist re-adicionaria o arquivo velho.
    Resultado: árvore = main novo + o que o persist produziu fora dos caminhos
    do feeder. Devolve {'arquivos', 'trazidos', 'apagados'}.
    """
    head_sha = _sha(head, cwd)
    classe, arquivos = classifica_avanco(base, head_sha, cwd)
    if classe != "feeder":
        raise AvancoNaoEhDoFeeder(
            f"{len(fora_do_feeder(arquivos))} arquivo(s) fora de FEEDER_PATHS "
            f"(ou base não é ancestral de {head})")

    rc, _, err = _git(["reset", "--soft", head_sha], cwd)
    if rc != 0:
        raise RuntimeError(f"git reset --soft {head_sha}: {err}")

    existentes, apagados = [], []
    for p in arquivos:
        rc, _, _ = _git(["cat-file", "-e", f"{head_sha}:{p}"], cwd)
        (existentes if rc == 0 else apagados).append(p)

    if existentes:
        rc, _, err = _git(["checkout", head_sha, "--"] + existentes, cwd)
        if rc != 0:
            raise RuntimeError(f"git checkout {head_sha} -- ...: {err}")
    if apagados:
        rc, _, err = _git(["rm", "-q", "--cached", "--ignore-unmatch", "--"] + apagados, cwd)
        if rc != 0:
            raise RuntimeError(f"git rm --cached ...: {err}")
        root = cwd or os.getcwd()
        for p in apagados:
            try:
                os.remove(os.path.join(root, p))
            except FileNotFoundError:
                pass
    return {"arquivos": arquivos, "trazidos": len(existentes), "apagados": len(apagados)}


def _cli(argv):
    if len(argv) != 3 or argv[0] not in ("classifica", "aplica"):
        print("uso: persist_reconcile.py classifica|aplica <base> <head>", file=sys.stderr)
        return RC_ERRO
    cmd, base, head = argv
    try:
        if cmd == "classifica":
            classe, arquivos = classifica_avanco(base, head)
            fora = set(fora_do_feeder(arquivos))
            print(f"avanço do main ({base} → {head}): {len(arquivos)} arquivo(s) tocado(s) → {classe}")
            for p in arquivos:
                print(("  ! " if p in fora else "    ") + p)
            if classe == "outro" and not eh_ancestral(base, head):
                print("  base não é ancestral de head (force-push?) — reingest completo")
            return RC_FEEDER if classe == "feeder" else RC_OUTRO
        r = aplica_avanco_feeder(base, head)
        # UMA linha: o persist_snapshot.sh captura e completa com o tempo gasto
        print(f"avanço só do feeder: {len(r['arquivos'])} arquivos "
              f"({r['trazidos']} trazidos de {head}, {r['apagados']} apagados)")
        return RC_FEEDER
    except AvancoNaoEhDoFeeder as e:
        print(f"recusado: {e}", file=sys.stderr)
        return RC_OUTRO
    except Exception as e:  # noqa: BLE001 — qualquer falha = rc 1, o bash cai no reingest
        print(f"erro: {e}", file=sys.stderr)
        return RC_ERRO


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
