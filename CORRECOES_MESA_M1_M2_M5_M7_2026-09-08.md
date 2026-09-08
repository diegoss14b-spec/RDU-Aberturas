# Correções Mesa — M1, M2, M5, M7 — 8 setembro 2026

Base isolada: `9247c5bcf1935c7b643e74abf7c2ed977a896e0a`, repositório oficial RDU-Aberturas.
Não houve deploy, push, disparo de workflow, captura de casas ou alteração do banco real.
M3/M4/M6/M8 pertencem a outra frente; esta entrega não altera fórmulas de precificação.

## Mudanças e contratos para preservar

- **M1 — relógio da observação.** Ingest usa `captured_at` da linha original, com SHA-256 da linha, separado de `last_ingested_at`. Reingest de mesmo horário ou horário mais antigo não incrementa observações/ticks, nem rejuvenesce o fechamento. `last_seen_observed_at` faz dedupe; `last_observed_at` pertence à última odd pré-jogo realmente usada. Observação live não substitui preço/horário pré-jogo. Horário ausente, ilegível ou >60s no futuro não é inventado. Horário sem fuso segue o contrato BRT existente. Fechamento requer observação comprovada; merge carrega os metadados da observação escolhida, nunca de outra linha.
- **M2 — CLV qualificado.** O gate compartilhado exige abertura e fechamento verificados, coerentes com os seus horários observados e close até 60 minutos antes do kickoff. Faixas: ≤15m, 15–30m, 30–60m, >60m, desconhecido. A fronteira mínima pré-jogo continua 45s. Histórico antigo permanece no banco e nas telas, mas sem entrar no CLV estrito. Abre→Fecha mantém os valores legados com rótulos explícitos de horário não verificado ou última captura distante. Não chamar toda última captura de fechamento próximo do apito.
- **M5 — cartões sem fallback silencioso.** `cards_r2` documentado é a contagem exata; sem vermelhos confirmados, R1=R2. Vermelhos sem incidentes dão intervalo `[cards, cards+red_cards]`. Se todos os valores possíveis dão o mesmo vencedor, liquida-se só o vencedor, com `result=null`, intervalo e retry para o total exato. Se muda green/red/push, fica `pending_semantics`, incluído no backlog. Resultado incompatível com os limites também é pendente. `cards` sozinho não é prova de R2. Metadados e revisão viajam para keys, CLV e ledger. Registros antigos de cartões sem contrato são rotulados “legado · a revalidar”, não reinterpretados silenciosamente.
- **M7 — descoberta e revisita.** Orçamento máximo permanece 60 eventos Betano/120 7k, sem aumentar a captura. Pelo menos 25% vai para candidatos não visitados/mais antigos; restante prioriza jogos com estatística útil. 7k deixa de excluir para sempre jogos com menos de 60 mercados: entram na descoberta limitada. O parser de família/período permanece o mesmo. Erros de transporte não significam ausência de mercado. Estado/métricas em `data/odds/_status/{betano,7k}_discovery.json`; o pipeline já preserva esse diretório em persist/reconciliação. Estado de fila expira em sete dias, sem tocar odds históricas. Não foi demonstrada cobertura total da oferta em produção: a fila cria a oportunidade de descobri-la e mede o que ficou fora do orçamento.

## Histórico e revisões: não fabricar passado

O CLV estrito deve começar com amostra baixa após o rollout. Isso é esperado e explicado na tela. Não marcar timestamps antigos como verificados para recuperar os números do painel.

`review_legacy_settlements.py --keys-dir CAMINHO_DE_UMA_COPIA/keys` emite um plano JSON **somente leitura**, hashes dos arquivos e candidatos à reconciliação. Não aplica uma migração e não produz timestamps reconstruídos. Uma futura migração dos registros antigos deve:

1. Congelar e hashear uma cópia integral de keys/ledger/CLV/results, inclusive arquivos arquivados.
2. Reconciliar incidentes por identidade e regra documentadas; comparar resultado e vencedor antes/depois.
3. Preservar original e anexar revisão explícita; permitir rollback por restauração da cópia.
4. Validar P&L e contagens antes/depois em dry-run e obter revisão antes da execução real.

O ledger/CLV é um log append-only: identidade de emissão é `(key, settlement_revision)`. Uma revisão **não é nova aposta**. Para análises externas, usar `settlement_revisions.latest_revisions` sobre shards na ordem de append; não somar linhas brutas. A UI lê keys consolidadas, então não duplica o P&L. Liquidações exatas antigas não são automaticamente recalculadas por toda mudança do feed; correções retrospectivas continuam exigindo revisão controlada.

## Verificação e rollout

- `python3 -m pytest -q --disable-warnings` em checkout isolado; inclui replay do mesmo JSON em dois horários, observação nova com preço idêntico, ordem invertida, live após pré-jogo, missing/future, merge sem empréstimo de proveniência, limites 15/30/60 minutos, cartões com vermelho direto/segundo amarelo, resultado invariável/ambíguo, revisões idempotentes, fila ultrapassando o antigo prefixo fixo e plano legado sem writes.
- Não rodar os builders/capturadores contra a pasta Claude/stubs. Publicação da Mesa somente pelo fluxo oficial GitHub Actions após revisão do root.
- Depois do primeiro ciclo autorizado, conferir status de descoberta, idades reais, nenhum tick novo no reingest do mesmo snapshot, nenhuma linha `pending_semantics` marcada como push, nenhuma revisão duplicada por chave, e rótulos do legado na UI. Repetir suíte no checkout completo final e validar visualmente.
- Não reativar Escanteios por tabela: a configuração de mercado desligado foi preservada.

## Limitações transparentes

`captured_at` prova o horário declarado pelo capturador, não o horário de alteração interna da odd na casa. Não se assume que todas as casas expõem esse horário. O timestamp do evento é conservador quando a coleta possui várias chamadas. Fontes podem ter atrasos legítimos; as travas não provam que uma odd ainda estava apostável.
Sem auditoria externa do resultado, não afirmar que houve uma aposta liquidada errada: esta correção elimina o fallback que tornava esse risco possível.
