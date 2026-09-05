# Feeder da Mesa — port Windows do pinnacle_feeder_local.sh do Mac (1 ciclo por
# execução; agendado a cada 35 min pela tarefa RDU_FeederMesa).
# A Pinnacle exige IP residencial — por isso este loop roda em casa, não na nuvem.
# Lição de 23/08: push só conta se ENTROU em origin/main — o log do Mac dizia
# "pushed" sem ter empurrado e 3 levas ficaram presas. Aqui o push é VERIFICADO
# (fetch + ancestral); falhou mesmo após retry → PUSH_FALHOU + reset --hard.
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "data", "_feeder_win.log")
LOCK = os.path.join(ROOT, "data", "_feeder_win.lock")

GIT_TIMEOUT = 300
FETCH_TIMEOUT = 1500  # superbet full já levou 7 min; folga


def log(msg):
    ts = datetime.now().strftime("%d/%m %H:%M")
    line = f"[{ts}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args, timeout=GIT_TIMEOUT):
    """Roda comando no ROOT; devolve (rc, saida). rc=124 em timeout."""
    try:
        p = subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT {' '.join(args)}"


def git(*args, timeout=GIT_TIMEOUT):
    return run(["git"] + list(args), timeout=timeout)


def limpa_estado_git():
    """Desarma rebase/merge preso e lock órfão — a raiz do feeder travado de
    26/08: um `pull --rebase` que estourou o timeout no meio deixou
    `.git/rebase-merge` + arquivos UU, e todo ciclo seguinte falhava no commit."""
    gitdir = os.path.join(ROOT, ".git")
    if os.path.isdir(os.path.join(gitdir, "rebase-merge")) or \
       os.path.isdir(os.path.join(gitdir, "rebase-apply")):
        git("rebase", "--abort")
        log("estado de rebase preso — abortado no arranque")
    if os.path.exists(os.path.join(gitdir, "MERGE_HEAD")):
        git("merge", "--abort")
        log("merge preso — abortado no arranque")
    lock = os.path.join(gitdir, "index.lock")
    if os.path.exists(lock):
        try:
            if time.time() - os.path.getmtime(lock) > 120:  # só se claramente órfão
                os.remove(lock)
                log("index.lock órfão removido")
        except OSError:
            pass


def sync_repo():
    """Base limpa = origin/main, SEM rebase. O feeder só ADICIONA arquivos de
    dados e re-captura tudo a cada ciclo, então commit local não-enviado é
    descartável (design do .sh do Mac) — `fetch`+`reset --hard` nunca conflita,
    ao contrário do `pull --rebase` que replayava commit preso e travava."""
    limpa_estado_git()
    git("fetch", "-q", "origin", "main", timeout=GIT_TIMEOUT)
    rc, out = git("reset", "-q", "--hard", "origin/main")
    if rc != 0:
        log(f"ABORTA: reset --hard origin/main falhou: {out.strip()[:200]}")
        return False
    return True


PUSH_RETRIES = 3  # rejeições seguidas toleradas (main avançando de novo no meio)


def _delta_do_feeder(commit, add_paths):
    """(alterados, apagados) que `commit` fez em relação ao PAI dele, restrito
    aos caminhos do feeder. É exatamente o que o ciclo mudou — inclusive o
    snapshot antigo que a rotação de 2 gerações apaga. None se o git falhar."""
    args = ["diff", "--name-status", "--no-renames", "-z", f"{commit}~1", commit]
    if add_paths:
        args += ["--", *add_paths]
    rc, out = git(*args)
    if rc != 0:
        return None
    alterados, apagados = [], []
    campos = out.split("\0")  # STATUS \0 path \0 STATUS \0 path \0 ...
    for status, path in zip(campos[0::2], campos[1::2]):
        if not status or not path:
            break
        (apagados if status.startswith("D") else alterados).append(path)
    return alterados, apagados


def push_verificado(commit_msg, add_paths=()):
    """True só se HEAD local está contido em origin/main após o push.

    Push rejeitado = origin/main avançou entre o sync e o push (snapshot da
    Mesa, outro feeder). Auditoria de 05/09/2026: a versão antiga fazia
    `fetch` + `reset --soft origin/main` + `commit`, mas o INDEX ainda era a
    árvore inteira do ciclo (avô + arquivos do feeder) → o commit novo REVERTIA
    tudo que o main tinha ganhado no meio (ticks/, keys/, ledger/, valor/data,
    snapshots das outras casas: 4 casos reais, ~10 mil linhas de ticks apagadas).
    Agora: guarda o sha do commit local (old), `reset --hard origin/main` (base
    = o main NOVO, inteiro) e traz de `old` SÓ o delta do feeder — arquivos que
    o ciclo alterou/criou (checkout old -- …) e os que apagou (git rm) — dentro
    dos `add_paths`. Commit e push de novo; se o main andar outra vez, repete
    com fetch fresco até PUSH_RETRIES. NUNCA `pull --rebase` (o trap de 26/08),
    NUNCA `reset --soft` de árvore inteira."""
    rc, _ = git("push", "-q", "origin", "HEAD:main")
    tentativa = 0
    while rc != 0 and tentativa < PUSH_RETRIES:
        tentativa += 1
        rc_h, old = git("rev-parse", "HEAD")
        old = old.strip()
        git("fetch", "-q", "origin", "main", timeout=GIT_TIMEOUT)
        delta = _delta_do_feeder(old, add_paths) if rc_h == 0 else None
        if delta is None:
            log(f"push rejeitado e não deu pra isolar o delta do feeder em {old[:9]} — desisto")
            return False
        alterados, apagados = delta
        if not alterados and not apagados:
            # 05/09/2026 (revisão): o commit do ciclo nasce de `git add <add_paths>`,
            # então delta vazio = parse quebrado ou add_paths errado. Seguir daria
            # reset --hard + "nothing to commit" + CONFIRMADO sem ter empurrado nada.
            log(f"push rejeitado e o delta do feeder em {old[:9]} veio VAZIO — desisto")
            return False
        log(f"push rejeitado (main avançou) — reconciliando só os caminhos do feeder: "
            f"{len(alterados)} alterados, {len(apagados)} apagados (tentativa {tentativa}/{PUSH_RETRIES})")
        # ⚠️ daqui em diante HEAD == origin/main, então a verificação final
        # (is-ancestor) passaria de graça: toda falha tem que devolver False
        # explicitamente, senão o ciclo loga "CONFIRMADO" sem ter empurrado nada.
        rc_r, out = git("reset", "-q", "--hard", "origin/main")
        if rc_r != 0:
            log(f"reset --hard origin/main falhou: {out.strip()[:200]}")
            return False
        ok = True
        if alterados:
            # só arquivos que EXISTEM em old (A/M) — caminho sem arquivo novo nem entra
            rc_c, out = git("checkout", old, "--", *alterados)
            ok = rc_c == 0
        if ok and apagados:
            # deleção do feeder respeitada SÓ no que ele mesmo apagou; se o main
            # já não tem o arquivo, --ignore-unmatch deixa passar
            rc_d, out = git("rm", "-q", "-r", "--ignore-unmatch", "--", *apagados)
            ok = rc_d == 0
        if not ok:
            log(f"reconciliação falhou: {out.strip()[:200]}")
            return False
        rc_c, out_c = git("commit", "-q", "-m", commit_msg)
        if rc_c != 0:
            # "nothing to commit" = o main novo já traz byte a byte o que o
            # feeder fez; aí HEAD == origin/main é o resultado certo.
            if "nothing to commit" in out_c or "nada" in out_c:
                break
            log(f"commit da reconciliação falhou: {out_c.strip()[:200]}")
            return False
        rc, _ = git("push", "-q", "origin", "HEAD:main")
    git("fetch", "-q", "origin", "main", timeout=GIT_TIMEOUT)
    rc_v, _ = git("merge-base", "--is-ancestor", "HEAD", "origin/main")
    return rc_v == 0


def n_events(status_rel):
    try:
        with open(os.path.join(ROOT, status_rel), encoding="utf-8") as f:
            return int(json.load(f).get("n_events", 0) or 0)
    except Exception:
        return 0


def ciclo_casa(casa, fetch_script, status_rel, add_paths):
    ts = datetime.now().strftime("%d/%m %H:%M")
    rc, out = run([sys.executable, fetch_script], timeout=FETCH_TIMEOUT)
    if rc != 0:
        log(f"{casa}: fetch falhou rc={rc} ({out.strip()[-200:]})")
    n = n_events(status_rel)
    if n <= 0:
        log(f"{casa}: fetch local devolveu 0 — nada pushed")
        return

    msg = f"{casa}: feed local {ts} ({n} eventos) [skip ci]"
    git("add", *add_paths)
    rc_c, out_c = git("commit", "-q", "-m", msg)
    if rc_c != 0:
        if "nothing to commit" in out_c or "nada" in out_c:
            log(f"{casa}: {n} eventos mas sem mudança nos arquivos — nada a commitar")
        else:
            log(f"{casa}: commit falhou ({out_c.strip()[:200]})")
            limpa_estado_git()
            git("reset", "-q", "--hard", "origin/main")
        return

    if push_verificado(msg, add_paths):
        log(f"{casa}: feed ok: {n} eventos, push CONFIRMADO em origin/main")
    else:
        log(f"PUSH_FALHOU {casa} ({n} eventos) — reset pra origin/main, recaptura no próximo ciclo")
        limpa_estado_git()
        git("reset", "-q", "--hard", "origin/main")


def main():
    # trava anti-sobreposição (ciclo pode passar de 35 min se a rede rastejar)
    if os.path.exists(LOCK):
        idade = time.time() - os.path.getmtime(LOCK)
        if idade < 2 * 3600:
            log(f"lock presente ({idade/60:.0f} min) — outro ciclo em andamento, saindo")
            return 0
        log(f"lock velho ({idade/3600:.1f} h) — assumindo ciclo morto, seguindo")
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    try:
        if not sync_repo():
            return 1
        ciclo_casa(
            "pinnacle", "fetch_odds_pinnacle.py", "data/odds/_status/pinnacle.json",
            ["data/odds/pinnacle_latest*.json",
             "data/odds/_snapshots/pinnacle_full_*.jsonl",
             "data/odds/_status/pinnacle.json"],
        )
        ciclo_casa(
            "superbet", "fetch_odds_superbet.py", "data/odds/_status/superbet.json",
            ["data/odds/superbet_latest*.json",
             "data/odds/_snapshots/superbet_full_*.jsonl",
             "data/odds/_status/superbet.json",
             "data/odds/_status/superbet_diag.json"],
        )
        return 0
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
