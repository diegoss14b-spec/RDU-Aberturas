#!/usr/bin/env bash
# Persistência resiliente do snapshot de odds + histórico (Mesa de Aberturas).
#
# Substitui o antigo "aborta se main avançou" (que congelava a Mesa) por um laço
# que RE-MESCLA sem perder tick: quando origin/main avança durante a rodada
# (auto-colisão de rodadas enfileiradas, ou push externo do feed de resultados),
# reseta pra base nova, restaura a captura DESTA rodada e re-roda o pipeline de
# histórico. O ingest é upsert idempotente por chave (une A⊎B: contadores somados,
# abertura mais antiga, fechamento mais recente), então nenhuma variação de odds
# é perdida — e o board sempre chega ao deploy. Ver análise 2026-07-18.
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
    && python history_settle.py && python build_history.py && python build_moves.py || return 1
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

for attempt in 1 2 3 4 5; do
  git fetch origin main
  if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    reingest_on_new_base || { echo "::error::re-ingest sobre a base nova falhou"; exit 1; }
  fi
  stage
  if git diff --cached --quiet; then
    echo "Sem artefatos novos para commitar."
    exit 0
  fi
  git commit -m "odds: snapshot $(date -u +%Y%m%d_%H%M) [$MODE] [skip ci]"
  if git push origin HEAD:main; then
    echo "Snapshot persistido e enviado (tentativa $attempt)."
    exit 0
  fi
  echo ">> push rejeitado (tentativa $attempt) — main avançou de novo; desfaço o commit e re-tento sobre a base nova"
  git reset --soft HEAD~1
  sleep 3
done

echo "::error::persist falhou após 5 tentativas — não deu pra reconciliar com main"
exit 1
