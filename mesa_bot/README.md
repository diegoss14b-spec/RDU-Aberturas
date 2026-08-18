# mesa-bot — sinais Mesa → Telegram

Lê os flags de valor já publicados em `board.js` da Mesa de Aberturas e envia
ao Telegram. **Não recalcula** μ / EV — a fonte de verdade é o `build_board` da Mesa.

## Fase 1 (esta)

```bash
# dry-run com board local
python3 mesa-bot/run_once.py --path valor-app/valor/data/board.js --dry-run

# dry-run com board ao vivo
python3 mesa-bot/run_once.py --url https://valor-rdu.netlify.app/data/board.js --dry-run

# envio real (precisa das vars)
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python3 mesa-bot/run_once.py --url https://valor-rdu.netlify.app/data/board.js
```

Config opcional: copiar `config.example.json` → `config.json`.

Na nuvem: workflow `valor-app/.github/workflows/mesa_signals.yml` (cron ~15 min),
secrets `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`. O código do bot é espelhado em
`valor-app/mesa_bot/` para o Actions do repo RDU-Aberturas.

Anti-spam: `data/sent.json` (chave `sofa_id|mercado|linha|lado|casa`). Reenvia se
a odd mudar ≥2% ou o EV subir ≥1 p.p. Board com mais de 90 min → um alerta
“Mesa parada” (cooldown 1 h), sem inventar sinal.

## Fase 2 (roadmap — não implementada)

Ambiente conjunto do jogo: deep-link da mensagem já aponta para a **Prévia do Jogo**
(`?lg=&hm=&aw=`). Depois: enriquecer Prévia/Mesa com odds das casas no mesmo card.

## Fase 3 (roadmap — não implementada)

Recovery vivo: quando o board envelhece, além do alerta, API/Action diagnostica
`_status/*.json` / último `valor.yml`, re-dispara captura e reporta no Telegram.
LLM só se o diagnóstico mecânico não bastar. O `watchdog.yml` da Mesa já cobre
parte disso.

## O que este bot NÃO faz

- Não altera captura / `build_board` / deploy da Mesa
- Não usa `mesa-paralela`
- Não usa Grok/LLM na Fase 1
