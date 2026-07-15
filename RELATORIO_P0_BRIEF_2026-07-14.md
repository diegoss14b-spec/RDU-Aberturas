# Relatório P0 — Brief auditoria 2026-07-14 (Mesa de Aberturas)

**Data implementação:** 2026-07-14  
**Repo:** `valor-app/RDU Aberturas`  
**Deploy:** **realizado 2026-07-14 ~02:45 BRT** — valor-rdu + rdustats

---

## Resumo

Implementados os quatro blocos P0 da Mesa:

| Bloco | Status |
|---|---|
| Push / EV em linhas inteiras | Feito |
| Ladders 7k + saneamento de pares | Feito |
| Kickoff / stale / actionable | Feito |
| Shadow vs production | Feito |

P1 (histórico/CLV, RDUStats, árbitros, jogadores) **não** foi executado nesta rodada.

---

## Antes → Depois

### 1. Matemática O/U (push)

| | Antes | Depois |
|---|---|---|
| Pricer | `p_under = 1 - p_over` | `p_over_win`, `p_under_win`, `p_push` |
| Linha inteira L | push embutido no under | `P(X>L)`, `P(X<L)`, `P(X=L)` |
| Meia linha | ok por acaso | `p_push = 0` explícito |
| EV | `p * odd - 1` | `p_win * odd + p_push - 1` |
| Edge | `p_modelo - p_devig` (universos mistos) | `p_cond - p_devig` com `p_cond = p_win/(1-p_push)` |
| Odd justa | `1/p` | `(1-p_push)/p_win` |

**Arquivos:** `pricing_math.py` (novo), `value_pricers.py`, `candidate_pricer.py`, `build_board.py`, `valor/js/valor.js`

### 2. 7k / ladders

| | Antes | Depois |
|---|---|---|
| Merge | todos `MarketType` com mesmo `canon()` mesclados | famílias isoladas por `market_type_id` |
| Escolha | last-write-wins | família com mais linhas / preferência “total” / id estável |
| Saneamento board | só range de odds | margem `[0, 12%]`, linhas únicas, monotonia over/under |
| Relatório | — | `data/odds/_status/ladder_rejects.json` |

**Arquivos:** `fetch_odds_7k.py`, `build_board.py` (`sanitize_ou_ladder`), `gate_board.py`

### 3. Actionable

| | Antes | Depois |
|---|---|---|
| Kickoff | flags em jogo iniciado | `game_state`: upcoming/started/finished/unknown |
| Valor server | qualquer jogo com modelo | só `upcoming` + casa não stale |
| UI valor | filtro “Próx. 90 min” opcional | hard gate: kickoff futuro; board >5h desabilita |
| Confiança | podia 70+ em iniciado | hard cap ≤49 se iniciado/stale/desconhecido |
| Board card | — | badge de estado + strip “não acionável” |

### 4. Shadow vs production

| | Antes | Depois |
|---|---|---|
| `BOARD.valor` | `candidate_pricer` (shadow) | `value_pricers` (production) |
| Override | implícito no import | só com `ALLOW_SHADOW_BOARD=1` |
| Shadow dual-run | — | `data/odds/_status/shadow_valor.json` (fora da UI) |
| Gate deploy | cobertura só | bloqueia se `model.status` ≠ production/promoted ou flags shadow |

---

## Testes

```text
python -m unittest test_pricing_push -v   → 17/17 OK
python test_canonical.py                  → 8/8 OK
python test_history_quality.py            → 8/8 OK
python build_board.py                     → model=production/value_pricers
```

Dry-run local (odds stale da captura anterior):

- `casas=['Betano']`, 33 jogos, **0 flags de valor** (jogos já started/finished — correto)
- `skip kickoff/started=7` confirma o gate
- `model=production/value_pricers`

---

## Critérios de aceite P0 (checklist)

### §3 Push
- [x] Testes Poisson/NB em 7 / 7.5 / 8 / 8.5  
- [x] `p_over + p_under + p_push ≈ 1`  
- [x] Meia linha `p_push == 0`  
- [x] EV over inteiro também com push  
- [x] Settlement já usava push (`history_settle`); alinhado com pre-game  
- [x] Regressões de ordem de grandeza (U7/U8 superestimados no bug antigo)  
- [x] Sinal só com EV/edge corrigidos  
- [x] UI marca linha inteira (`push` tag)

### §4 7k
- [x] Famílias não mescladas por canon sozinho  
- [x] Valor só com par saneado  
- [x] Margem min/max no board  
- [x] Gate com anomalia sistêmica de ladder  
- [x] Teste monotonia 8.5/9/9.5  
- [ ] Relatório dos 169 pares da captura auditada (precisa re-captura 7k fresca)

### §5 Kickoff/stale
- [x] Zero valor acionável com kickoff ≤ now (server + UI + gate)  
- [x] Alta confiança bloqueada em iniciado/stale  
- [x] Testes timezone/virada  
- [x] UI diferencia estados  

### §6 Shadow
- [x] Rebuild padrão = production  
- [x] Shadow em arquivo separado  
- [x] Gate falha se shadow no board  
- [x] Override só via env explícita  

---

## O que NÃO foi feito (próximos passos)

1. **Deploy** — aguardar OK do Diego  
2. **Re-captura 7k** + rebuild com odds frescas para validar volume de flags pós-push  
3. **P1 histórico/CLV** — dedupe sofa, settlement Escanteios/SOT/Desarmes, CLV gate, n_moves  
4. **P1 RDUStats** — espelhos, nulls, cartões, árbitros, jogadores  
5. **Backfill competições** — pausado (~63/216)  

---

## Arquivos tocados

```
pricing_math.py                 (novo)
value_pricers.py
candidate_pricer.py
build_board.py
fetch_odds_7k.py
gate_board.py
test_pricing_push.py            (novo)
valor/js/valor.js
valor/js/board.js
valor/index.html
RELATORIO_P0_BRIEF_2026-07-14.md
```

**Sem deploy. Sem `build_site.py`. Sem promoção de shadow.**
