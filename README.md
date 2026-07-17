# Mesa de Aberturas (valor)

A Mesa publica o site [valor-rdu.netlify.app](https://valor-rdu.netlify.app/) a partir deste repositório. A atualização principal roda na nuvem e não depende do computador local para capturar odds ou publicar o board.

## Operação automática

Existem dois workflows:

- `.github/workflows/valor.yml`: rodada **full** a cada hora, no minuto `:07`, e capturas de fechamento em `:22`, `:37` e `:52` quando há jogo em até 90 minutos.
- `.github/workflows/watchdog.yml`: verifica o site nos minutos `:17` e `:47`; se o board ultrapassar 90 minutos e não houver execução ativa, dispara uma rodada full.

A rodada full executa:

1. captura paralela de Betano, Superbet, 7k, EstrelaBet e Pinnacle;
2. atualização do calendário canônico SofaScore;
3. persistência do inventário completo em snapshots estáveis;
4. migração e ingestão do histórico, fechamento, liquidação, CLV e movimentos;
5. montagem do board e do painel operacional;
6. gates de qualidade por casa, mercado, fixtures, cobertura e precificação;
7. commit versionado e envio dos status, snapshots, fixtures e histórico;
8. publicação no Netlify somente após a persistência ser confirmada.

Uma captura de fechamento alimenta os ticks e o close, mas nunca substitui o board completo.

## Contratos de segurança

- Ponteiros só são aceitos quando o arquivo-alvo existe, o JSONL inteiro é válido, a contagem confere e o timestamp é interpretável.
- Uma captura parcial ou com colapso relevante de algum mercado não substitui o último full saudável.
- Snapshots promovidos são imutáveis; o pointer é trocado por último e a geração anterior é preservada para leitores concorrentes.
- Fulls entre 2 e 12 horas podem permanecer visíveis como inventário stale-keep, mas não geram flags de valor.
- O deploy falha se faltar arquivo crítico, upload exigido ou confirmação `ready` do Netlify.
- O gate bloqueia colapsos relevantes de casa/mercado, Sofa inválido ou defasado, baixa cobertura de fixtures nos sinais, odds inconsistentes, mercados de três vias e jogos iniciados.
- Board com mais de 120 minutos — ou timestamp inválido — desativa os sinais acionáveis.

## Histórico, CLV e liquidação

O banco fica em `data/odds_history/`.

- `ticks/`: mudanças observadas antes do kickoff.
- `keys/`: abertura, última odd, close real, extremos, movimentos e resultado por instrumento.
- `results/results_auto.json`: feed local de resultados.
- `results/settlement_status.json`: backlog retryable por mercado, motivo e idade.
- `clv/`: registros liquidados.

As métricas agregadas usam um sinal determinístico por jogo+mercado, em vez de contar casas, linhas alternativas e os dois lados como apostas independentes. Push permanece no denominador do ROI com lucro zero. O close observado e o kickoff são exibidos separadamente.

No PC, a tarefa `RDUValorResults` roda às 08:00 e 20:00. Ela executa `C:\Users\diego\Desktop\Claude\export_results_for_valor.py` e envia o feed ao repositório sem depender do estado do checkout local. O exportador cobre cartões, faltas, finalizações, impedimentos, laterais, tiros de meta, escanteios, chutes no gol e desarmes.

## Segredos do GitHub

Em **Settings → Secrets and variables → Actions**:

- `NETLIFY_TOKEN`: publicação do site.
- `DECODO_USER` e `DECODO_PASS`: proxy com saída brasileira para casas que bloqueiam runners estrangeiros.

## Execução e validação

`workflow_dispatch` no workflow principal força uma rodada full.

Antes de publicar alterações de código, execute os arquivos `test*.py`, valide os JavaScripts com `node --check` e confira `git diff --check`. O painel **Operação** do site mostra frescor, cobertura por casa/mercado, saúde do Sofa e backlog de liquidação.
