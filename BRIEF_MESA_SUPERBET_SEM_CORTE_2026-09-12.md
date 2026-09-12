# Mesa — Superbet sem corte por quantidade de jogos

Data: 12/09/2026. Escopo: repositório oficial `diegoss14b-spec/RDU-Aberturas`, branch `main`.

Este brief descreve a correção de fonte. A confirmação de publicação e os números efetivamente capturados devem ser consultados no recibo de validação posterior, na pasta compartilhada. Código enviado não significa captura concluída.

## Problema confirmado

A Mesa autenticada, filtrada por Superbet e Faltas, exibia 26 partidas e terminava em Lazio–Milan, às 13h de 12/09. Não havia paginação nem limite nesse renderizador: ele exibia todos os jogos elegíveis recebidos.

O capturador aplicava `[:MAX_EVENTS]`, com `MAX_EVENTS=900`, depois do filtro temporal de 30 horas. Inventário consultado às 03:15:12 BRT: 1.837 eventos na resposta, 1.375 dentro da janela, somente 900 selecionados e 475 omitidos. A fronteira estava às 13h. Os 475 não são 475 jogos com faltas: são eventos cujo detalhe não era consultado.

Consultas individuais confirmaram pares ativos de faltas em Casa Pia–Porto (14h), Real Madrid–Rayo (16h) e Palmeiras–São Paulo (18h30), além do corte. A listagem resumida desses jogos tinha somente odds principais de resultado; ausência de faltas na listagem não prova ausência no detalhe.

## Correção a preservar

- Consultar todos os eventos únicos elegíveis dentro do horizonte, sem limite dos primeiros N. Janela full de 30 horas e janela close existentes mantidas.
- Detalhes consultados com sessões persistentes independentes por thread e agendamento contínuo: uma consulta lenta não bloqueia o preenchimento das outras vagas.
- Concorrência padrão continua em 3; limite externo continua em 900 segundos e orçamento interno em 840 segundos. Não ampliar automaticamente consumo de outras casas.
- Cada linha recebe o horário real da resposta, não o horário do início da captura inteira.
- Arquivo de saída único por execução; nenhuma troca de ponteiro durante a captura. Falhas ou eventos não consultados impedem a promoção de uma captura incompleta, preservando o último full.
- HTTP 404 fica contabilizado separadamente como detalhe indisponível. HTTP 200 com corpo nulo, vazio/inválido ou identidade divergente não conta como consulta válida sem mercados.
- Diagnóstico atualizado atomicamente em `_status/superbet_diag.json`, separando inventário, elegíveis, eventos agendados, respostas concluídas, falhas, indisponíveis e não agendados. `detail_requests` inclui tentativas repetidas; não é número de jogos distintos.
- `run_capture.py` não repete o catálogo inteiro após `CaptureIncomplete`: as consultas individuais já tiveram tentativas limitadas. `CaptureBudgetExceeded` permanece sem repetição integral.
- Parser de mercados de jogo inteiro e de equipe preservado; não misturar primeiro tempo, jogador ou handicap com total de partida.

O painel Operação recebe o diagnóstico somente quando compatível com a captura informada, sem renovar artificialmente o horário do full ou alterar a classificação de saúde das outras casas.

Não há alteração na paginação da aba Linhas, na interface da Mesa, nos modelos, na Central de Cartões, em `matches.json`, em credenciais, nos limites da Bet365, nem nas escolhas de mercados habilitados.

## Verificações

Testes cobrem mais de 1.200 eventos, ausência do corte de 900, deduplicação, janela full/close, timestamps, respostas inválidas, HTTP 404, primeiro detalhe lento, limite de concorrência, orçamento esgotado e preservação de ponteiros. O teste do renderizador exibe 1.207 dos 1.210 jogos fornecidos; os três excluídos são incompatíveis com os filtros ou já iniciados, não um teto.

Antes da publicação, executar toda a suíte `test_*.py`, `node --check` para os JavaScripts relevantes e `git diff --check`. Após a rodada oficial, verificar individualmente cobertura da Superbet, novo full, jogos posteriores às 13h no board e na interface autenticada, manifesto, hashes dos artefatos e preservação de histórico/CLV.

Uma captura futura pode continuar limitada por falha da fonte ou tempo disponível; nesse caso precisa falhar explicitamente, não declarar que consultou todos. Esta correção não é garantia de 100% dos mercados da casa nem amplia o horizonte para além de 30 horas.

## Continuidade no Windows

1. Incorporar a fonte do repositório oficial, preservando alterações locais, credenciais, feeders, snapshots e histórico. Não recolocar `MAX_EVENTS=900` nem outro corte silencioso.
2. Publicar somente pelo workflow oficial `valor.yml`. Não usar `deploy_netlify.py valor`, `value_bets_run.py` nem os espelhos/stubs locais do site.
3. Não iniciar full paralelo; respeitar a execução ativa e a serialização existente.
4. Não remover gates de cobertura, identidade, frescor, liquidação, CLV ou manifesto para forçar publicação.
5. Conferir o recibo posterior antes de afirmar que os jogos adicionais estão publicados. O pacote de fonte não deve incluir dados, caches nem segredos.
