#!/usr/bin/env bash
# 25/08: a reconciliação re-roda build_board — o corte de mercados NUNCA pode depender
# só do env do chamador (3ª porta do gotcha 46a). Default = o mesmo do valor.yml.
export MERCADOS_OFF="${MERCADOS_OFF:-escanteios}"
# Persistência resiliente do snapshot de odds + histórico (Mesa de Aberturas).
#
# Substitui o antigo "aborta se main avançou" (que congelava a Mesa) por um laço
# que RE-MESCLA sem perder tick: quando origin/main avança durante a rodada
# (auto-colisão de rodadas enfileiradas, ou push externo do feed de resultados),
# reseta pra base nova, restaura a captura DESTA rodada e re-roda o pipeline de
# histórico. O ingest é upsert idempotente por chave (une A⊎B: contadores somados,
# abertura mais antiga, fechamento mais recente), então nenhuma variação de odds
# é perdida — e o board sempre chega ao deploy. Ver análise 2026-07-18.
# 05/09/2026: esse re-ingest virou o caminho CARO (caso B); o avanço só do feeder local
# (caso A, o comum) reconcilia em segundos via persist_reconcile.py — ver bloco abaixo.
#
# Lê MODE e GATE_OUTCOME do ambiente (setados pelo workflow).
set -uo pipefail

MODE="${MODE:-}"
GATE="${GATE_OUTCOME:-}"

git config user.name "valor-bot"
git config user.email "actions@github.com"

# guarda a captura crua DESTA rodada (sobrevive ao git reset --hard)
SAVE="$(mktemp -d)"
cleanup() { rm -rf "$SAVE"; }
trap cleanup EXIT
mkdir -p "$SAVE/odds" "$SAVE/status" "$SAVE/fixtures"
cp -a data/odds/*_latest*.json "$SAVE/odds/" 2>/dev/null || true
[ -d data/odds/_snapshots ]   && cp -a data/odds/_snapshots   "$SAVE/odds/_snapshots"   2>/dev/null || true
[ -d data/odds/_status ]      && cp -a data/odds/_status/.    "$SAVE/status/"           2>/dev/null || true
cp -a data/fixtures/sofa_latest*.json "$SAVE/fixtures/" 2>/dev/null || true
[ -d data/fixtures/_snapshots ] && cp -a data/fixtures/_snapshots "$SAVE/fixtures/_snapshots" 2>/dev/null || true

stage() {
  git add -A -- data/odds/_status data/odds_history \
    valor/data/history.js valor/data/moves.js valor/data/ops.js
  # dataset abertura×fechamento (§8: crítico no full, mas em close/parcial pode não existir)
  [ -d data/odds/openclose ]      && git add -A -- data/odds/openclose
  [ -f valor/data/openclose.js ]  && git add -A -- valor/data/openclose.js
  if [ "$MODE" = "full" ] && [ "$GATE" = "success" ]; then
    git add -A -- valor/data/board.js
    # §8 — manifesto atômico do build acompanha o board no commit
    [ -f valor/data/manifest.js ] && git add -A -- valor/data/manifest.js
    for p in data/odds/*_latest_full.json data/odds/_snapshots \
             data/fixtures/sofa_latest.json data/fixtures/sofa_latest_data.json \
             data/fixtures/_snapshots; do
      [ -e "$p" ] && git add -A -- "$p"
    done
  fi
}

# ── GH001 (02/09): rejeição por ARQUIVO GRANDE é estrutural — retry não resolve. ──
# O pre-receive do GitHub recusa blob >100 MB e o laço antigo tratava QUALQUER push
# rejeitado como "main avançou", gastando 5 tentativas × ~6min subindo ~700 MB de pack
# pra nada (ledger/2026-08.jsonl chegou a 149,92 MB quando o feed de resultados de
# 01/09 liquidou 10 dias de backlog de uma vez). Duas defesas fail-closed:
#  1) antes do commit: arquivo staged ≥95 MB aborta AGORA, com nome e tamanho no log
#     (os writers fatiam via jsonl_shard.py — se isto disparar, algum arquivo NOVO
#     passou a crescer sem teto e precisa do mesmo tratamento);
#  2) depois do push: rejeição mencionando GH001/pre-receive/file size aborta sem
#     re-tentar (só non-fast-forward continua no laço de reconciliação).
GH_LIMITE_BYTES=95000000

checa_tamanho_staged() {
  local estourou=0 f sz
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    sz=$(wc -c < "$f")
    if [ "$sz" -ge "$GH_LIMITE_BYTES" ]; then
      echo "::error::persist: '$f' tem $((sz / 1048576)) MB (limite do GitHub: 100 MB) — abortado ANTES do push; fatiar o arquivo (ver jsonl_shard.py)"
      estourou=1
    fi
  done < <(git diff --cached --name-only)
  return "$estourou"
}

# reconcilia sobre a base nova sem perder tick: reset -> restaura captura -> re-ingest
reingest_on_new_base() {
  echo ">> main avançou: reconciliando o histórico sobre a base nova (merge por chave, sem perda)"
  git reset --hard origin/main || return 1
  cp -a "$SAVE/odds/"*_latest*.json data/odds/ 2>/dev/null || true
  [ -d "$SAVE/odds/_snapshots" ] && mkdir -p data/odds/_snapshots && cp -a "$SAVE/odds/_snapshots/." data/odds/_snapshots/ 2>/dev/null || true
  [ -d "$SAVE/status" ]         && mkdir -p data/odds/_status    && cp -a "$SAVE/status/."          data/odds/_status/    2>/dev/null || true
  cp -a "$SAVE/fixtures/"*.json data/fixtures/ 2>/dev/null || true
  [ -d "$SAVE/fixtures/_snapshots" ] && mkdir -p data/fixtures/_snapshots && cp -a "$SAVE/fixtures/_snapshots/." data/fixtures/_snapshots/ 2>/dev/null || true
  python migrate_history_keys.py && python history_ingest.py && python history_close.py \
    && python history_settle.py && python build_model_ledger.py \
    && python build_history.py && python build_moves.py || return 1
  # §8 — openclose é crítico: se falhar na reconciliação, aborta (não republica meio-build)
  python build_openclose.py || return 1
  if [ "$MODE" = "full" ] && [ "$GATE" = "success" ]; then
    python build_board.py || return 1
    python build_ops.py || return 1
    # regenera o manifesto sobre a base reconciliada (mesmo build) — se falhar, aborta
    python build_manifest.py || return 1
  else
    python build_ops.py || true
  fi
  return 0
}

# ── Corrida com o FEEDER LOCAL (05/09/2026): avanço "só do feeder" reconcilia BARATO. ──
# Auditoria de 05/09: 11 de 48 runs morreram no teto de 75 min do job DENTRO deste passo.
# Causa: cada avanço do main durante o persist re-rodava o pipeline inteiro (~15-18 min
# no runner), e o feeder local da Pinnacle/Superbet avança o main a cada ~23 min tocando
# SÓ os caminhos dele (data/odds/{pinnacle,superbet}_latest*, _snapshots/{pinnacle,
# superbet}_full_*, _status/{pinnacle,superbet}*.json) — nada que o pipeline precise
# re-processar. Agora o avanço é CLASSIFICADO (persist_reconcile.py, `git diff base→main`):
#   A) só caminhos do feeder → reconciliação barata: reset --soft pro main novo + checkout
#      desses caminhos por cima da árvore local (apagado lá = apagado aqui); ticks/keys/
#      ledger/valor/data/*.js desta rodada ficam intactos. Segundos, sem re-rodar nada.
#   B) qualquer outro arquivo (outra rodada da Mesa, feed de resultados, código) →
#      reingest_on_new_base como antes. Dúvida (helper falhou, force-push) = B, porque o
#      reset --hard do reingest é o único caminho garantidamente seguro.
# O laço ganhou voltas (5 → 12) porque em A cada volta custa segundos; o nº de reingests
# COMPLETOS continua limitado em 5, como era. Sentinela de 95 MB e abort GH001 intactos.
PERSIST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_TENTATIVAS=12
MAX_REINGEST=5
n_reingest=0

reconcilia_com_main() {
  local t0=$SECONDS rc resumo
  python "$PERSIST_DIR/persist_reconcile.py" classifica HEAD origin/main; rc=$?
  if [ "$rc" -eq 0 ]; then
    resumo="$(python "$PERSIST_DIR/persist_reconcile.py" aplica HEAD origin/main)"; rc=$?
    if [ "$rc" -eq 0 ]; then
      echo ">> ${resumo}, reconciliado em $((SECONDS - t0))s — sem re-rodar o pipeline"
      return 0
    fi
    echo "::warning::reconciliação barata falhou (rc=$rc) — caio no reingest completo"
  elif [ "$rc" -ne 3 ]; then
    echo "::warning::classificação do avanço do main falhou (rc=$rc) — assumo caso B (reingest completo)"
  fi
  n_reingest=$((n_reingest + 1))
  if [ "$n_reingest" -gt "$MAX_REINGEST" ]; then
    echo "::error::persist: $MAX_REINGEST reingests completos e o main segue avançando fora dos caminhos do feeder"
    return 1
  fi
  reingest_on_new_base || { echo "::error::re-ingest sobre a base nova falhou"; return 1; }
}

attempt=0
while [ "$attempt" -lt "$MAX_TENTATIVAS" ]; do
  attempt=$((attempt + 1))
  git fetch origin main
  if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    reconcilia_com_main || exit 1
  fi
  stage
  if git diff --cached --quiet; then
    echo "Sem artefatos novos para commitar."
    exit 0
  fi
  checa_tamanho_staged || exit 1
  git commit -m "odds: snapshot $(date -u +%Y%m%d_%H%M) [$MODE] [skip ci]"
  push_out="$(git push origin HEAD:main 2>&1)"; push_rc=$?
  printf '%s\n' "$push_out"
  if [ "$push_rc" -eq 0 ]; then
    echo "Snapshot persistido e enviado (tentativa $attempt)."
    exit 0
  fi
  if printf '%s' "$push_out" | grep -qiE 'GH001|pre-receive hook declined|exceeds GitHub'; then
    echo "::error::push rejeitado pelo PRE-RECEIVE (arquivo grande/GH001) — estrutural, retry não resolve; ver o arquivo apontado acima"
    exit 1
  fi
  echo ">> push rejeitado (tentativa $attempt) — main avançou de novo; desfaço o commit e re-tento sobre a base nova"
  git reset --soft HEAD~1
  sleep 3
done

echo "::error::persist falhou após $MAX_TENTATIVAS tentativas ($n_reingest reingests completos) — não deu pra reconciliar com main"
exit 1
