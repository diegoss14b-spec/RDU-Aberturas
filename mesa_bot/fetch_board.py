# -*- coding: utf-8 -*-
"""Baixa board.js da Mesa e devolve o dict BOARD."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DEFAULT_BOARD_URL = "https://valor-rdu.netlify.app/data/board.js"


def _strip_js_assign(text: str) -> str:
    """Aceita `window.BOARD={...};` ou JSON puro."""
    t = (text or "").strip()
    if not t:
        raise ValueError("board vazio")
    # remove BOM
    if t.startswith("\ufeff"):
        t = t[1:]
    m = re.match(
        r"^(?:window\.)?BOARD\s*=\s*",
        t,
        flags=re.I,
    )
    if m:
        t = t[m.end():]
    t = t.rstrip()
    if t.endswith(";"):
        t = t[:-1].rstrip()
    return t


def parse_board_text(text: str) -> Dict[str, Any]:
    raw = _strip_js_assign(text)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("BOARD não é objeto JSON")
    return data


def load_board_file(path: Path) -> Dict[str, Any]:
    return parse_board_text(path.read_text(encoding="utf-8"))


def fetch_board(
    url: str = DEFAULT_BOARD_URL,
    *,
    timeout: int = 45,
) -> Tuple[Dict[str, Any], str]:
    """GET board.js → (BOARD dict, texto cru)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mesa-bot/1.0 (+valor-rdu signals)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", "replace")
    return parse_board_text(text), text


def load_board(
    *,
    url: Optional[str] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    if path is not None:
        return load_board_file(Path(path))
    board, _ = fetch_board(url or DEFAULT_BOARD_URL)
    return board
