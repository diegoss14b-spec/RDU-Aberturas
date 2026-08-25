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


def sync_repo():
    rc, out = git("pull", "--rebase", "-q", "origin", "main")
    if rc != 0:
        log(f"pull --rebase falhou ({out.strip()[:200]}) — reset pra origin/main")
        git("rebase", "--abort")
        rc2, out2 = git("reset", "--hard", "origin/main")
        if rc2 != 0:
            log(f"ABORTA: reset --hard falhou: {out2.strip()[:200]}")
            return False
    return True


def push_verificado():
    """True só se HEAD local está contido em origin/main após o push."""
    rc, _ = git("push", "-q", "origin", "HEAD:main")
    if rc != 0:
        git("pull", "--rebase", "-q", "origin", "main")
        rc, _ = git("push", "-q", "origin", "HEAD:main")
    git("fetch", "-q", "origin", "main")
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

    git("add", *add_paths)
    rc_c, out_c = git("commit", "-q", "-m", f"{casa}: feed local {ts} ({n} eventos) [skip ci]")
    if rc_c != 0:
        if "nothing to commit" in out_c or "nada" in out_c:
            log(f"{casa}: {n} eventos mas sem mudança nos arquivos — nada a commitar")
        else:
            log(f"{casa}: commit falhou ({out_c.strip()[:200]})")
        return

    if push_verificado():
        log(f"{casa}: feed ok: {n} eventos, push CONFIRMADO em origin/main")
    else:
        log(f"PUSH_FALHOU {casa} ({n} eventos) — reset pra origin/main, recaptura no próximo ciclo")
        git("rebase", "--abort")
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
