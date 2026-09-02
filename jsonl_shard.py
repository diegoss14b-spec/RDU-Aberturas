# -*- coding: utf-8 -*-
"""jsonl_shard.py — append mensal de JSONL com rolagem de fatia (02/09/2026).

## Por que existe

Mesmo incidente das keys/ (ver history_shard.py), agora nos ARQUIVOS APPEND-ONLY:
`ledger/2026-08.jsonl` chegou a 149,92 MB no runner e o GitHub rejeita no
pre-receive qualquer arquivo acima de 100 MB (GH001). A Mesa parou de PERSISTIR
na noite de 01/09: o feed de resultados (push esporádico, cadência ~semanal)
destravou a liquidação de ~10 dias de backlog DE UMA VEZ — history_settle
appendou ~29 MB no clv/ e build_model_ledger ~65 MB no ledger/ do MESMO mês do
kickoff — e o push passou a ser rejeitado nas 5 tentativas do persist, em loop,
a cada rodada. A rajada não é anomalia: o próprio history_settle documenta que
`no_result_source` fica retryable "se a fonte um dia cobrir, liquida sozinho".
O armazenamento é que precisa aguentar a rajada.

## O desenho: o monólito do mês CONGELA no teto e a escrita rola pra fatia nova

- `{YYYY-MM}.jsonl` é a primeira fatia (compatível com o histórico já publicado
  e com quem olha o diretório); quando uma escrita for estourar MAX_BYTES, o
  arquivo congela como está e a continuação vai pra `{YYYY-MM}.p001.jsonl`,
  depois `p002`, … Lote maior que o teto é DIVIDIDO entre quantas fatias
  precisar (é exatamente o caso da rajada pós-feed).
- Nada é reescrito nem renomeado: fatia cheia nunca mais muda (bom pros deltas
  do git e pro princípio do ledger imutável — lição do CLV do chasing).
- Ao contrário das keys/ (history_shard), aqui a ORDEM entre fatias não carrega
  semântica: cada linha é um registro independente e autossuficiente. Os
  consumidores (backtest/dedupe) leem a UNIÃO das fatias.

## Quem usa

- build_model_ledger.emit_ledger — appenda liquidadas; dedupe lê todas as fatias.
- history_settle._append_clv — appenda o registro imutável do estudo de CLV.
- persist_snapshot.sh tem a sentinela: arquivo staged ≥95 MB aborta o persist
  com nome e tamanho no log, sem gastar 5 tentativas de push de ~700 MB.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Mesmo teto por fatia das keys/ (history_shard.MAX_BYTES): 4x de folga sobre o
# limite duro de 100 MB do GitHub e abaixo dos 50 MB do warning.
MAX_BYTES = 25_000_000

_PART_RE = re.compile(r"\.p(\d{3,})\.jsonl$")


def month_jsonl_paths(dir_, month):
    """Arquivos do mês, na ordem: monólito `{month}.jsonl` + fatias `pNNN`."""
    dir_ = Path(dir_)
    out = []
    mono = dir_ / f"{month}.jsonl"
    if mono.exists():
        out.append(mono)
    out.extend(sorted(dir_.glob(f"{month}.p*.jsonl")))
    return out


def _next_idx(paths):
    mx = 0
    for p in paths:
        mo = _PART_RE.search(p.name)
        if mo:
            mx = max(mx, int(mo.group(1)))
    return mx + 1


def append_jsonl_month(dir_, month, lines, max_bytes=None):
    """Appenda linhas JSONL no mês, rolando pra fatia nova ao atingir o teto.

    ``lines``: strings já serializadas (sem ``\\n``) ou dicts (serializados aqui).
    Continua na ÚLTIMA fatia existente enquanto couber; lote maior que o teto é
    dividido. Toda fatia recebe ao menos 1 linha (linha única gigante não trava).
    Devolve a lista de arquivos tocados.
    """
    cap = int(max_bytes or MAX_BYTES)
    payload = [l if isinstance(l, str) else json.dumps(l, ensure_ascii=False)
               for l in lines]
    if not payload:
        return []
    dir_ = Path(dir_)
    dir_.mkdir(parents=True, exist_ok=True)
    existing = month_jsonl_paths(dir_, month)
    target = existing[-1] if existing else dir_ / f"{month}.jsonl"
    size = target.stat().st_size if target.exists() else 0
    next_idx = _next_idx(existing)
    touched = []
    buf = []

    def flush():
        nonlocal buf
        if not buf:
            return
        with target.open("a", encoding="utf-8") as fh:
            fh.write("".join(buf))
        if target not in touched:
            touched.append(target)
        buf = []

    for line in payload:
        nbytes = len(line.encode("utf-8")) + 1
        if size and size + nbytes > cap:
            flush()
            target = dir_ / f"{month}.p{next_idx:03d}.jsonl"
            next_idx += 1
            size = 0
        buf.append(line + "\n")
        size += nbytes
    flush()
    return touched
