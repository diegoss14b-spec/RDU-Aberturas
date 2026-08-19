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

## Review 19/08 (o que mudou e por quê)

- **`mesa_shared.py`** (raiz do valor-app): limiares do gate, `FIXTURE_LABEL_COMP`,
  `LEAGUE_FORA`, `three_way` e `de_vig` agora são UM módulo importado pelo
  `build_board` **e** pelo juiz — a cópia espelhada que vivia no `judge.py`
  driftaria em silêncio.
- **Paridade vigiada**: `audit_mesa_bot_parity.py` roda ANTES de cada ciclo no
  Actions e falha o job se o juiz divergir do `BOARD.valor` (ΔEV≠0, chave a
  mais/a menos). Fail-closed: sem sinal até consertar.
- **Barreira LEAGUE_FORA no fallback**: o juiz não preça mais jogo cujo rótulo
  da casa é de recorte excluído (feminino/base/copa/2ª div) — a Mesa já vetava,
  o fallback de identidade do bot reintroduzia.
- **Dedup no Actions consertado**: `actions/cache` com chave fixa NUNCA regrava
  em hit — o estado congelou no 1º run (skip_dedup=0 em todos os ciclos). Agora
  restore/save separados com chave única por run (família `v2`).
- **Token saneado**: o secret veio com whitespace no meio e TODO envio morria com
  “URL can't contain control characters” (30+ runs verdes sem entregar nada).
  `resolve_credentials` remove whitespace de token/chat.
- **Partida a frio (`seed_flood_guard`, default 10)**: estado vazio + backlog →
  registra tudo SEM enviar (1 resumo só) e sinal novo flui do ciclo seguinte.
  Sem isso, o 1º ciclo real despejaria 25 mensagens velhas.
- **Alerta de falha de fetch** entrou no cooldown de infra (1×/h), como o de
  board stale.
- **Degradação por mercado**: bundle sem `reds`+`regime` derruba SÓ cartões
  (igual à Mesa), não o juiz inteiro.

⚠ **Fonte de verdade: `valor-app/mesa_bot/`** (o Actions roda daqui). O espelho
`Claude/mesa-bot/` é sincronizado por `audit_mesa_bot_mirror.py --sync` (no
monorepo); drift é erro, não detalhe.

## Fora de escopo (decisão em aberto com o Diego)

- **Team totals** (`jogos[].times`): nem a Mesa nem o bot flaggeiam — estender
  é decisão de produto (Mesa + bot JUNTOS, nunca só o bot).
- **Teto de idade do board pra sinal** (ex.: >6h não envia, só alerta) — hoje
  `judge_when_stale` julga sempre.
- **Recovery vivo** (re-disparo de captura): watchdog da Mesa cobre; Fase 3.

## Roadmap

- **UI do jogo**: deep-link já aponta à Prévia (`?lg=&hm=&aw=`).
- **Recovery**: watchdog da Mesa + alerta; re-disparo vivo fica pra depois.
