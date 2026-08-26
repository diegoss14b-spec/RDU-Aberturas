# -*- coding: utf-8 -*-
"""archive_odds_history.py — fix C (26/08/2026): tira do caminho QUENTE do banco de odds
os meses JÁ LIQUIDADOS, pra o passo 'Banco de odds' do CI parar de re-ler 100+MB por
ciclo sem necessidade (o odds_history chegou a 1,1GB; o I/O do runner do GitHub sobre
isso estourava o teto de 18min → manifesto stale → deploy bloqueado → Mesa congelada).

O QUE MOVE: só `keys/` e `ticks/` (os arquivos GRANDES que 4 scripts re-globam todo
ciclo). NÃO mexe em `ledger/` nem `clv/` (o registro imutável do estudo/CLV — pequeno
e é fonte de verdade do backtest).

PRA ONDE: data/odds_history/_archive/{keys,ticks}/ — DENTRO do repo. Nada é apagado,
tudo reversível (`--revert`). Só sai do glob `keys/*.json` que os leitores de ciclo usam.

QUEM CONTINUA VENDO OS ARQUIVADOS: só o build_history.py (aba CLV/estudo) — ele glob
`keys/*.json` + `_archive/keys/*.json`. Os outros (history_close, history_settle,
build_model_ledger, build_moves) globam SÓ o dir quente e passam a pular o mês liquidado
de propósito: fecham/liquidam/movem/ledgeram apenas jogos recentes (julho já foi).

MÊS LIQUIDADO = último dia do mês + 7 dias (SEM_FONTE_INTERVAL do history_settle) < hoje.
Assim o mês corrente E o anterior-ainda-liquidando NUNCA são arquivados por engano.

Idempotente: mês já no _archive não é remexido. Fail-safe: nunca arquiva o mês corrente.
"""
import sys, os, shutil, re, json
from pathlib import Path
from datetime import date, timedelta, datetime, timezone

ROOT = Path(__file__).resolve().parent
HIST = ROOT / "data" / "odds_history"
ARCH = HIST / "_archive"
SUBS = ("keys", "ticks")               # só os grandes/hot-globbed; ledger+clv ficam
SETTLE_LAG = timedelta(days=7)         # = SEM_FONTE_INTERVAL do history_settle
MONTH_RE = re.compile(r"^(\d{4}-\d{2})")   # keys: 2026-07.pNNN.json · ticks: 2026-07-10.jsonl


def _last_day_of_month(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[5:7])
    nm = date(y + (m == 12), (m % 12) + 1, 1)
    return nm - timedelta(days=1)


def _settled_months(today: date):
    """meses cujos jogos já não mudam mais (fim do mês + folga de liquidação < hoje)."""
    out = set()
    for sub in SUBS:
        d = HIST / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            mo = MONTH_RE.match(f.name)
            if not mo:
                continue
            ym = mo.group(1)
            if _last_day_of_month(ym) + SETTLE_LAG < today:
                out.add(ym)
    return sorted(out)


def archive(dry=False, today=None):
    today = today or datetime.now(timezone.utc).date()
    cur_month = today.strftime("%Y-%m")
    ARCH.mkdir(parents=True, exist_ok=True)
    settled = [m for m in _settled_months(today) if m != cur_month]   # trava dupla no corrente
    moved, bytes_moved = [], 0
    for sub in SUBS:
        src_dir = HIST / sub
        dst_dir = ARCH / sub
        if not src_dir.is_dir():
            continue
        for f in sorted(src_dir.iterdir()):
            mo = MONTH_RE.match(f.name)
            if not mo or mo.group(1) not in settled:
                continue
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f.name
            sz = f.stat().st_size
            if dry:
                moved.append((str(f), str(dst), sz)); bytes_moved += sz
                continue
            if dst.exists():          # já arquivado num run anterior (settled=imutável): descarta o quente
                f.unlink()
            else:
                shutil.move(str(f), str(dst))
            moved.append((str(f), str(dst), sz)); bytes_moved += sz
    print(f"[archive] meses liquidados: {settled or '(nenhum)'} · "
          f"{'SIMULARIA' if dry else 'moveu'} {len(moved)} arquivos "
          f"({bytes_moved/1048576:.0f} MB) p/ _archive/")
    for s, d, sz in moved[:12]:
        print(f"    {Path(s).parent.name}/{Path(s).name}  ({sz/1048576:.0f} MB)")
    if len(moved) > 12:
        print(f"    … +{len(moved)-12} arquivos")
    if moved and not dry:
        log = ARCH / "_archive_log.jsonl"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "months": settled, "n_files": len(moved),
                                 "mb": round(bytes_moved / 1048576, 1)}, ensure_ascii=False) + "\n")
    return len(moved)


def revert():
    """desfaz: traz tudo do _archive de volta pro dir quente (pra rodar backtest completo local)."""
    n = 0
    for sub in SUBS:
        d = ARCH / sub
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            dst = HIST / sub / f.name
            if not dst.exists():
                shutil.move(str(f), str(dst))
                n += 1
    print(f"[archive] revert: {n} arquivos de volta ao dir quente")
    return n


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    else:
        archive(dry="--dry-run" in sys.argv)
