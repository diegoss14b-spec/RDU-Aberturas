#!/bin/bash
# Alimentador LOCAL da Pinnacle (14/08/2026) — a nuvem está bloqueada pela Pinnacle
# desde 10/08 (Azure e Decodo recebem vazio); o Mac residencial lê de graça.
# Roda o fetch oficial da Mesa localmente e empurra os arquivos pro repo a cada ~19min.
# O pointer de latest_full é protegido pelo capture_common (fetch da nuvem com 0 eventos
# não rebaixa o inventário), então o dado local flui pros passos seguintes das Actions.
cd /Users/diego14b/Desktop/Claude/valor-app
LOG=data/_pinnacle_feeder.log
while true; do
  TS=$(date '+%d/%m %H:%M')
  git pull --rebase -q origin main 2>>"$LOG" || { git rebase --abort 2>/dev/null; git reset --hard origin/main >>"$LOG" 2>&1; }
  python3 fetch_odds_pinnacle.py >>"$LOG" 2>&1
  N=$(python3 -c "import json;print(json.load(open('data/odds/_status/pinnacle.json')).get('n_events',0))" 2>/dev/null)
  if [ "${N:-0}" -gt 0 ]; then
    git add data/odds/pinnacle_latest*.json data/odds/_snapshots/pinnacle_full_*.jsonl data/odds/_status/pinnacle.json 2>>"$LOG"
    git commit -q -m "pinnacle: feed local $TS ($N eventos) [skip ci]" 2>>"$LOG"
    git push -q origin HEAD:main 2>>"$LOG" || { git pull --rebase -q origin main && git push -q origin HEAD:main; } >>"$LOG" 2>&1
    echo "[$TS] feed ok: $N eventos, pushed" >>"$LOG"
  else
    echo "[$TS] fetch local devolveu 0 — nada pushed" >>"$LOG"
  fi
  sleep 2100
done
