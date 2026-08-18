# mesa-bot — odds Mesa × modelo → Telegram

Lê **linhas e odds** do `board.js` da Mesa, precifica com o mesmo
`candidate_pricer` da Mesa/Prévia, aplica o gate (EV≥5%, edge≥4%, margem 0–12%,
P∈[15%,85%]) e envia valor novo no Telegram.

**Não é carteiro** do `BOARD.valor[]` — julga de novo a cada ciclo.

## Rodar

```bash
# dry-run com board ao vivo (precisa da pasta valor-app/ ao lado, com o bundle)
python3 mesa-bot/run_once.py --url https://valor-rdu.netlify.app/data/board.js --dry-run

# no repo RDU-Aberturas (Actions / local)
python3 mesa_bot/run_once.py --dry-run
```

Credenciais: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (env). Config opcional:
`config.example.json` → `config.json`.

Na nuvem: `.github/workflows/mesa_signals.yml` (cron ~15 min). O código vive em
`valor-app/mesa_bot/` no repo da Mesa.

Identidade dos times: `home_id`/`away_id`/`comp` no board (quando o `build_board`
já publicou) **ou** `data/fixtures/sofa_latest.json` no checkout.

Anti-spam: `data/sent.json` (`sofa_id|mercado|linha|lado|casa`). Board >90 min →
alerta “Mesa parada” (1×/h) **sem** parar o juiz (`judge_when_stale`).

## Roadmap

- **UI do jogo**: deep-link já aponta à Prévia (`?lg=&hm=&aw=`).
- **Recovery**: watchdog da Mesa + alerta; re-disparo vivo fica pra depois.
