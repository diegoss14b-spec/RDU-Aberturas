# -*- coding: utf-8 -*-
"""Envio Telegram (HTML). Sem token/chat = dry-run."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple


def resolve_credentials(
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Tuple[str, str]:
    tok = (token or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    cid = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    return tok, cid


def send_message(
    msg: str,
    *,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    dry_run: bool = False,
    timeout: int = 15,
) -> bool:
    """Envia HTML. True se enviou de verdade. dry_run ou sem credencial → False."""
    tok, cid = resolve_credentials(token, chat_id)
    if dry_run or not tok or not cid:
        print("DRY-RUN telegram:\n%s\n" % msg, flush=True)
        return False
    data = urllib.parse.urlencode({
        "chat_id": cid,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % tok
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8", "replace"))
            if resp.get("ok"):
                return True
            print("telegram recusou: %s" % (resp.get("description"),), flush=True)
            return False
        except Exception as e:
            last_err = e
            time.sleep(2)
    print("telegram erro: %s" % last_err, flush=True)
    return False
