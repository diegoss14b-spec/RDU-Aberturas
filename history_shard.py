# -*- coding: utf-8 -*-
"""history_shard.py — o documento do mês vira FATIAS ORDENADAS (31/07/2026).

## Por que existe

O `keys/2026-07.json` chegou a **100,96 MB** e o GitHub recusa no pre-receive qualquer
arquivo acima de **100 MB** (limite duro, não configurável). A Mesa parou de PERSISTIR
às 06:03 UTC de 31/07: o passo de commit tentava 5 vezes, era rejeitado nas 5, e cada
ciclo perdia os ticks recém-capturados. O board e o site seguiam no ar (o gate nem
chegava a rodar), então o sintoma era invisível na tela — só o e-mail de "run failed"
denunciava.

Anatomia (medida antes do conserto): 108.290 chaves / 104,5 MB, dos quais só ~39 MB
são VALORES. O resto é redundância estrutural — o nome de cada campo repetido em cada
registro e a chave longa escrita duas vezes.

## A escolha: fatiar PRESERVANDO A ORDEM

`{month}.p001.json`, `p002`… são pedaços consecutivos do MESMO documento: `load_month`
lê as fatias em ordem e reinsere as chaves na mesma sequência, então o dicionário
resultante é idêntico ao monólito — inclusive na ordem de iteração.

⚠️ **A ordem não é decoração, é requisito.** A primeira versão deste módulo fatiava por
CASA (superbet.json, betano.json…), o que parecia mais natural e dava shards menores.
O teste A/B contra o monólito reprovou: `moves.js` saiu com **27 linhas e 170 pontos a
menos**. A causa não é perda de chave (a união é byte-idêntica, sha256 conferido) — é
que `unify_keys_dict`, o dedup de confrontos, DEPENDE DA ORDEM em que as chaves
chegam: agrupado por casa ele encontrou 26 unificações em vez de 25, e mudar quem é o
"gid vencedor" reescreve as séries do gráfico. Séries que ficaram com menos de 2
pontos são descartadas na montagem, e some a linha. Ou seja: qualquer repartição que
reordene as chaves muda números publicados por um motivo que não é o dado. Fatiar em
ordem não tem esse problema por construção.

(A ordem-dependência do `unify_keys_dict` é uma fragilidade PRÉ-EXISTENTE — hoje a
ordem é o acaso histórico de inserção no monólito. Este módulo não a conserta; só
garante não tropeçar nela. Consertar de verdade é dar critério determinístico ao
desempate do dedup, e isso é mudança de produto, não de armazenamento.)

Alternativas descartadas: gzip do documento (o blob muda por inteiro a cada ciclo →
~10 MB de objeto novo a cada 15 min contra os deltas magros que o git faz hoje entre
JSONs parecidos); podar campos (a lição do CLV do chasing é NÃO podar — dado histórico
é o ativo); git-lfs (quota e mais uma dependência no runner).

## Quem precisou mudar

Praticamente ninguém: **todos os leitores já fazem `glob("keys/*.json")`** —
history_close, history_settle (`consolidate_key_documents`/`persist_consolidated_documents`),
build_model_ledger, build_history, build_moves, build_openclose, gate_board, migrate_*.
Nenhum deles sabe quantos arquivos existem por mês, e `persist_*` reescreve cada
documento NO LUGAR de onde o leu. O único ponto que nomeava `{month}.json` era o
`history_ingest`.

## Invariantes

- `load_month` devolve a UNIÃO das fatias na ORDEM das fatias. Chave repetida entre
  arquivos (não deve acontecer) é resolvida por `merge_records`.
- `save_month` só apaga o monólito legado e as fatias sobrando DEPOIS de gravar as
  novas com sucesso.
- Trava anti-encolhimento: gravar um mês com menos de `MIN_KEEP_RATIO` das chaves que
  estão no disco ABORTA (lição do build_unified_matches, que zerou 53 mil jogos).
- `MAX_BYTES` é o teto por fatia, com folga larga sobre os 100 MB do GitHub.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from history_merge import atomic_write_text, merge_records

# {month}.pNNN.json — o "." ordena ANTES de qualquer sufixo alfabético e o NNN
# zero-padded garante ordem numérica no glob (p002 < p010).
PART_RE = re.compile(r"^(\d{4}-\d{2})\.p(\d{3,})$")
MAX_BYTES = 25_000_000     # ~4-5 fatias hoje; 4x de folga sobre o limite do GitHub
MIN_KEEP_RATIO = 0.5       # anti-encolhimento


def _part_path(keys_dir, month, idx):
    return Path(keys_dir) / f"{month}.p{idx:03d}.json"


def month_paths(keys_dir, month):
    """Arquivos daquele mês, na ordem de leitura: monólito legado + fatias."""
    keys_dir = Path(keys_dir)
    out = []
    legacy = keys_dir / f"{month}.json"
    if legacy.exists():
        out.append(legacy)
    out.extend(sorted(keys_dir.glob(f"{month}.p*.json")))
    return out


def load_month(keys_dir, month):
    """União das chaves do mês, PRESERVANDO a ordem das fatias."""
    keys = {}
    for path in month_paths(keys_dir, month):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"{path.name} ilegível: {exc}") from exc
        if not isinstance(doc, dict):
            raise RuntimeError(f"{path.name} não é objeto JSON")
        for key, record in doc.items():
            if key in keys and isinstance(record, dict) and isinstance(keys[key], dict):
                keys[key] = merge_records(keys[key], record)
            else:
                keys.setdefault(key, record)
    return keys


def _fatiar(keys):
    """Corta o dicionário em pedaços de até MAX_BYTES, na ordem em que veio."""
    partes, atual, tam = [], {}, 2  # 2 = as chaves '{' e '}'
    for key, record in keys.items():
        # +len(par)+2 aproxima vírgula/dois-pontos; o corte não precisa ser exato,
        # só precisa ficar longe do teto.
        custo = len(json.dumps({key: record}, ensure_ascii=False).encode("utf-8"))
        if atual and tam + custo > MAX_BYTES:
            partes.append(atual)
            atual, tam = {}, 2
        atual[key] = record
        tam += custo
    if atual or not partes:
        partes.append(atual)
    return partes


def save_month(keys_dir, month, keys, log=print):
    """Grava o mês em fatias ordenadas. Só toca no que mudou.

    Devolve (n_gravadas, n_removidas, tamanhos_mb).
    """
    keys_dir = Path(keys_dir)
    existentes = month_paths(keys_dir, month)
    if existentes:
        antes = load_month(keys_dir, month)
        n_antes = sum(1 for k in antes if not str(k).startswith("__"))
        n_agora = sum(1 for k in keys if not str(k).startswith("__"))
        if n_antes and n_agora < n_antes * MIN_KEEP_RATIO:
            raise RuntimeError(
                f"anti-encolhimento: {month} tem {n_antes:,} chaves no disco e a gravação "
                f"traria {n_agora:,} (<{MIN_KEEP_RATIO:.0%}) — abortado sem escrever"
            )

    partes = _fatiar(keys)
    escritos, tamanhos = 0, []
    for idx, doc in enumerate(partes, start=1):
        path = _part_path(keys_dir, month, idx)
        texto = json.dumps(doc, ensure_ascii=False)
        tamanhos.append(len(texto.encode("utf-8")) / 1e6)
        try:
            atual = path.read_text(encoding="utf-8")
        except OSError:
            atual = None
        if atual != texto:
            atomic_write_text(path, texto)
            escritos += 1

    # sobras saem DEPOIS de todas as fatias novas estarem no disco — nunca antes.
    removidos = 0
    for path in sorted(keys_dir.glob(f"{month}.p*.json")):
        mo = PART_RE.match(path.stem)
        if mo and int(mo.group(2)) > len(partes):
            path.unlink()
            removidos += 1
    legacy = keys_dir / f"{month}.json"
    if legacy.exists():
        legacy.unlink()
        removidos += 1
        log(f"[shard] {legacy.name} fatiado em {len(partes)} pedaços "
            f"(maior: {max(tamanhos):.1f} MB) e removido")
    return escritos, removidos, tamanhos
