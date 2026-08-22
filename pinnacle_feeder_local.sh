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
  # 22/08 — SUPERBET também pelo Mac. O full da CI salvava só os ~134 primeiros jogos por
  # horário (kickoff até ~4h à frente) e os mercados da noite nunca entravam na Mesa; a
  # mesma captura rodada aqui (IP BR, direto) salva ~230 jogos / 39 com Faltas. Mesmo
  # loop (NÃO um 2º loop: dois git pull/commit/push no mesmo tree se atropelam e o
  # reset --hard do fallback apagaria o trabalho do outro). O pointer local é protegido
  # 75min pela guarda do capture_common (captured_by=local); Mac dormindo => a CI volta
  # a valer sozinha (degrada, não congela).
  TS=$(date '+%d/%m %H:%M')
  python3 fetch_odds_superbet.py >>"$LOG" 2>&1
  NS=$(python3 -c "import json;print(json.load(open('data/odds/_status/superbet.json')).get('n_events',0))" 2>/dev/null)
  if [ "${NS:-0}" -gt 0 ]; then
    git add data/odds/superbet_latest*.json data/odds/_snapshots/superbet_full_*.jsonl data/odds/_status/superbet.json data/odds/_status/superbet_diag.json 2>>"$LOG"
    git commit -q -m "superbet: feed local $TS ($NS eventos) [skip ci]" 2>>"$LOG"
    git push -q origin HEAD:main 2>>"$LOG" || { git pull --rebase -q origin main && git push -q origin HEAD:main; } >>"$LOG" 2>&1
    echo "[$TS] superbet feed ok: $NS eventos, pushed" >>"$LOG"
  else
    echo "[$TS] superbet local devolveu 0 — nada pushed" >>"$LOG"
  fi
  sleep 2100
done
