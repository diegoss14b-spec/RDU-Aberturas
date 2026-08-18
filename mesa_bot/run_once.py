# -*- coding: utf-8 -*-
"""Um ciclo: fetch board → juiz (modelo×odds) → dedup → Telegram.

O bot NÃO é carteiro do BOARD.valor[]. Ele:
  1. lê linhas/odds do board.js (Mesa)
  2. precifica com candidate_pricer (mesma régua da Mesa/Prévia)
  3. aplica gate EV/edge
  4. manda valor novo no Telegram

Uso:
  python3 mesa-bot/run_once.py --dry-run
  python3 mesa-bot/run_once.py --url https://valor-rdu.netlify.app/data/board.js
  python3 mesa_bot/run_once.py --dry-run   # no repo RDU-Aberturas

Credenciais: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (env). Não grave token em config.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from dedup import (  # noqa: E402
    filter_new, infra_cooldown_ok, load_state, mark_infra, mark_sent,
    prune_sent, save_state,
)
from fetch_board import DEFAULT_BOARD_URL, load_board  # noqa: E402
from format import (  # noqa: E402
    DEFAULT_PREVIA_BASE, format_board_stale, format_signal, format_summary,
)
from judge import judge_board  # noqa: E402
from signals import board_age_minutes  # noqa: E402
from telegram import send_message  # noqa: E402


def _load_config(path: Optional[Path]) -> dict:
    cfg = {
        "board_url": DEFAULT_BOARD_URL,
        "previa_base": DEFAULT_PREVIA_BASE,
        "stale_board_min": 90,
        "infra_cooldown_sec": 3600,
        "odd_rel_resend": 0.02,
        "ev_abs_resend": 1.0,
        "max_send_per_cycle": 25,
        "send_cycle_summary": False,
        # Board velho: ainda JULGA (odds podem servir); só alerta infra.
        "judge_when_stale": True,
        "state_path": str(_HERE / "data" / "sent.json"),
    }
    if path and path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update(user)
        except Exception as e:
            print("config inválido (%s): %s — usando defaults" % (path, e), flush=True)
    return cfg


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mesa odds × modelo → Telegram")
    ap.add_argument("--url", default=None, help="URL do board.js")
    ap.add_argument("--path", default=None, help="board.js local (pula fetch)")
    ap.add_argument("--config", default=None, help="config.json")
    ap.add_argument("--state", default=None, help="arquivo de dedup")
    ap.add_argument("--dry-run", action="store_true", help="não envia Telegram")
    ap.add_argument("--force-stale-alert", action="store_true",
                    help="ignora cooldown do alerta Mesa parada")
    ap.add_argument("--limit", type=int, default=None,
                    help="teto de mensagens neste ciclo")
    args = ap.parse_args(argv)

    cfg = _load_config(Path(args.config) if args.config else (_HERE / "config.json"))
    if args.state:
        state_path = Path(args.state)
        if not state_path.is_absolute():
            state_path = Path.cwd() / state_path
    elif cfg.get("state_path"):
        state_path = Path(cfg["state_path"])
        if not state_path.is_absolute():
            state_path = _HERE / state_path
    else:
        state_path = _HERE / "data" / "sent.json"

    url = args.url or cfg.get("board_url") or DEFAULT_BOARD_URL
    path = Path(args.path) if args.path else None

    try:
        board = load_board(url=url, path=path)
    except Exception as e:
        print("ERRO ao carregar board: %s" % e, flush=True)
        send_message(
            "⚠️ <b>MESA bot</b> — falha ao ler board.js\n<code>%s</code>" % e,
            dry_run=args.dry_run,
        )
        return 2

    gerado = board.get("gerado_iso") or board.get("gerado") or "?"
    age = board_age_minutes(board)
    stale_min = float(cfg.get("stale_board_min") or 90)
    state = load_state(state_path)
    prune_sent(state)

    print("board gerado=%s age_min=%s jogos=%d"
          % (gerado, ("%.1f" % age) if age is not None else "?",
             len(board.get("jogos") or [])),
          flush=True)

    board_stale = age is not None and age > stale_min
    if board_stale:
        print("BOARD STALE (%.0f > %.0f min) — alerta infra; juiz=%s"
              % (age, stale_min, "ON" if cfg.get("judge_when_stale", True) else "OFF"),
              flush=True)
        cooldown = int(cfg.get("infra_cooldown_sec") or 3600)
        if args.force_stale_alert or infra_cooldown_ok(state, cooldown_sec=cooldown):
            ok = send_message(
                format_board_stale(age, str(gerado)),
                dry_run=args.dry_run,
            )
            if ok or args.dry_run:
                mark_infra(state)
                save_state(state_path, state)
        else:
            print("alerta infra em cooldown", flush=True)
        if not cfg.get("judge_when_stale", True):
            return 0

    try:
        signals = judge_board(board)
    except Exception as e:
        print("ERRO no juiz: %s" % e, flush=True)
        send_message(
            "⚠️ <b>MESA bot</b> — falha no juiz (modelo×odds)\n<code>%s</code>" % e,
            dry_run=args.dry_run,
        )
        return 3

    to_send, skipped = filter_new(
        signals, state,
        odd_rel=float(cfg.get("odd_rel_resend") or 0.02),
        ev_abs=float(cfg.get("ev_abs_resend") or 1.0),
    )
    limit = args.limit if args.limit is not None else int(cfg.get("max_send_per_cycle") or 25)
    if limit >= 0:
        to_send = to_send[:limit]

    print("sinais=%d novos=%d skip_dedup=%d enviar=%d"
          % (len(signals), len(to_send), len(skipped), len(to_send)), flush=True)

    previa_base = cfg.get("previa_base") or DEFAULT_PREVIA_BASE
    n_ok = 0
    for sig in to_send:
        msg = format_signal(sig, previa_base=previa_base)
        sent = send_message(msg, dry_run=args.dry_run)
        if sent or args.dry_run:
            mark_sent(state, sig)
            n_ok += 1
            if not args.dry_run:
                import time
                time.sleep(0.35)

    save_state(state_path, state)

    if cfg.get("send_cycle_summary") and (n_ok or args.dry_run):
        send_message(
            format_summary(n_ok, len(skipped), len(signals), str(gerado)),
            dry_run=args.dry_run,
        )

    print("enviados=%d (dry_run=%s) state=%s"
          % (n_ok, args.dry_run, state_path), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
