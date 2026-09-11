# Mesa: captura, cobertura e publicação — 11/09/2026

Alterações autorizadas pelo Diego e implementadas pelo Codex. Este brief é o contrato de continuidade para o Mac e o Windows. O recibo de publicação acompanha o pacote no Drive.

## Proprietário e segurança

- Fonte oficial: repositório `diegoss14b-spec/RDU-Aberturas`, branch `main`.
- Publicação da Mesa SOMENTE pelo workflow `valor.yml`. Não publicar a pasta `Claude/valor`, não executar deploy local e não reconstruir o RDU Stats principal.
- Não substituir snapshots, pointers, odds_history, resultados, modelos, credenciais ou configurações de proxy pelos arquivos deste pacote. Ele contém somente código, testes e documentação.
- Preservar as proteções de histórico, identidade, frescor, shrink, manifesto e CLV. Nenhuma delas foi desativada.

## Bet365 / BetsAPI

1. Mantido `/v3/bet365/prematch`: a comparação pontual com v4 não mostrou ganho de cobertura; lote v4 apresentou timeout. Não migrar só por ser uma versão mais nova.
2. Full: até120 jogos, intervalo mínimo1h; close: até40 jogos, com pelo menos3min até o início. Limites de90 requests e420s por processo, incluindo retries internos. Orçamento esgotado não dispara retry completo imediato no orquestrador.
3. Seleção com nomes exatos de competição, partidas conhecidas úteis e pelo menos25% do orçamento para descoberta/rotação. Ligas não reconhecidas continuam elegíveis; simulações ficam fora.
4. Inventário de FIs: primeiras10 páginas +10 páginas profundas rotativas; mescla apenas identidades futuras até5 dias, invalidando kickoff corrigido. Não afirmar que uma rodada visitou todo o catálogo.
5. Lote que retorna9 de10 jogos tenta recuperar só o ausente. Resultados válidos já obtidos sobrevivem ao fim do orçamento. Falhas parciais não promovem o full e não recebem selo de sucesso.
6. Parser: aceita somente pares completos da mesma origem; não mistura lados entre blocos, rejeita preços/linhas não finitos, quarters e resultados exatos/três vias. `alternative_corners` foi removido do contrato de duas vias. Finalizações, chutes no gol, cartões, totais dos times e handicap mantidos.
7. Não interpretar `open:0` como mercado suspenso: semântica não comprovada. Nenhuma filtragem especulativa desse campo foi adicionada.
8. Reuso do full preserva o horário real, não consulta token/rede e exige contrato do parser atual. `parser_contract=2` identifica as novas observações.
9. Retenção: somente eventos não selecionados, ainda no inventário, mesmos participantes/kickoff, contrato2, relógio válido e idade≤12h. Mantém `captured_at` original; selecionados com resposta vazia não são ressuscitados. Retidos não satisfazem o piso mínimo de novas capturas.
10. Board Bet365: frescor por observação, não apenas pelo arquivo. Acima2h, sem sinal acionável de valor; acima12h, relógio ausente ou futuro inválido, omitido. O histórico continua recusando observações duplicadas/fora de ordem.

## Outras casas: mudanças e evidências

### 7k

O workflow34632976037 foi bloqueado porque desarmes caíram de13 para0 instrumentos e laterais/tiros de meta de13 para1. Não eram jogos encerrados: cinco partidas ainda futuras ficaram fora da seleção. A rodada selecionou73 jogos somente com escanteios,20 com outros mercados e27 vazios.

Limite da prova: o inventário bruto completo daquele ciclo não foi persistido. Os horários da fila provam que os cinco não foram inspecionados, mas não distinguem definitivamente exclusão pelo orçamento de ausência momentânea na listagem upstream. A prioridade inadequada por escanteios foi reproduzida e corrigida sem relaxar o gate.

Corrigido: escanteios isolados não consomem a reserva de jogos úteis (inclusive estado legado da fila), janela de5 dias e exclusão de simulações. O orçamento de120 consultas foi preservado. Escanteios continuam elegíveis à exploração, mas não deslocam os mercados ativos da Mesa.

Os desarmes de jogo e de ambos os times já eram reconhecidos: OU5518/5519/5520, confirmados em dois detalhes atuais. Não adicionar alias redundante alegando cobertura nova.

### Betano

Reconhecido o nome exato `Asiático (Mais/Menos) Total de Cartões`, jogo inteiro. No snapshot auditado havia11 pares: cinco novas linhas4,0 e seis linhas meia sobrepostas. Não são11 jogos/mercados novos.

`normalize_betano_markets` é agora a fonte única para board, reader histórico, contadores e utilidade da fila. Over/under vêm sempre do mesmo registro. Na duplicata, o total convencional tem prioridade; depois prioridade fixa de aba/primeiro par completo. Linhas inteiras usam devolução por igualdade; quarters continuam rejeitados.

Origem/família/semântica da linha seguem em metadados normalizados. O writer histórico legado não persiste todos esses metadados: não alegar migração de schema nem reescrever histórico antigo. A regra de contagem R1/R2 existente não foi alterada. A documentação oficial de cartões confirma vermelho=2; a liquidação segue as restrições de participantes da casa, não soma cega de incidentes.

Fonte oficial consultada: https://support.betano.bet.br/hc/pt-br/articles/6413994078365-Como-funciona-o-mercado-de-N%C3%BAmero-de-Cart%C3%B5es

### Sportingbet

Removidas exclusões por pedaços do nome: `gol` bloqueava Chutes no gol; `par` bloqueava clubes como Queens Park Rangers e Paris Saint-Germain. Agora o trecho estatístico passa por allowlist exata e o participante precisa corresponder inequivocamente a um dos times reais. Jogadores, tempos, gols e handicaps não são convertidos em totais FT.

Adicionada rotação/descoberta com o mesmo teto de200 detalhes. O defeito foi reproduzido no código; a consulta bruta atual respondeu403, então não há estimativa comprovada de quantas linhas extras estarão disponíveis em produção.

### EstrelaBet, Superbet e Pinnacle

- Estrela: rotação/descoberta no lugar do corte estático nos200 primeiros. Snapshot já tinha desarmes totais/times e linhas alternativas. Consulta bruta atual403 limitou a prova de novos nomes.
- Superbet: oito famílias FT já reconhecidas na amostra. Nenhum alias FT adicional comprovado. Parte da pesquisa foi em resposta ao vivo, usada só para taxonomia, nunca como odds pré-jogo.
- Pinnacle: inventário pontual de26.759 matchups mostrou Bookings/Corners, sem nova unidade estatística segura. Não inventar mapeamento de Faltas/Chutes/Desarmes.

## Mercados encontrados mas NÃO habilitados

Na7k existem cartões dos times no1º/2º tempo (OU6031–6034), além de cantos por período e três vias. Eles exigem período na identidade, liquidação e UI próprios. Não misturar com total do jogo inteiro para inflar cobertura. Props de jogadores e combinações continuam fora desta Mesa.

## Orquestração, publicação e painel

- Status distingue coleta nova, reuso/stride e fonte local preservada. Skip não renova timestamp nem entra como captura nova nas taxas futuras. Os registros históricos antigos não foram reclassificados sem evidência.
- Fontes residenciais protegidas, falhas de autenticação/geografia/parse e orçamento já esgotado não provocam retries improdutivos.
- Retry final: até2 casas paralelas e10min totais, em vez de somar todos os timeouts em sequência.
- Deploy: leitura pública com retries limitados para erro transitório; manifesto/histórico/manifesta novamente precisam ser da mesma geração. Se não houver baseline verificável, falha fechada. Política CLV, hashes e contagens continuam obrigatórios.
- Operação mostra a idade do full separada da última coleta. Um close recente não significa catálogo completo atualizado. Percentual de sucesso das capturas NÃO é percentual das linhas da casa.

## Arquivos estruturais para preservar

`bet365_capture_plan.py`, `fetch_odds_bet365.py`, `capture_discovery.py`, `fetch_odds_7k.py`, `fetch_odds_betano.py`, `fetch_odds_estrelabet.py`, `fetch_odds_sportingbet.py`, `bookmaker_contracts.py`, `capture_common.py`, `capture_health.py`, `run_capture.py`, `build_board.py`, `history_ingest.py`, `build_ops.py`, `deploy.py`, `valor/js/board.js`, `valor/js/ops.js` e testes de regressão de11/09.

## Procedimento no Windows

1. Identificar o clone OFICIAL da Mesa; conferir `git status` e remoto. Não tocar no checkout do RDU principal.
2. Se limpo, atualizar pelo `origin/main` preservando commits locais. Se houver alterações concorrentes, comparar e reconciliar por arquivo; não copiar o ZIP cegamente sobre uma versão mais nova.
3. Preferir os commits oficiais ao pacote. O ZIP é referência/recuperação somente de fonte e testes. Nunca sobrescrever feeds/credenciais/histórico.
4. Executar `python -m pytest test_*.py -q` e verificações de sintaxe dos JS alterados.
5. Manter os publicadores de feeds residenciais existentes, sem transformar o Windows em publicador do site da Mesa. Não disparar full adicional enquanto houver rodada em andamento.
6. Conferir o recibo final: commit publicado, workflow, manifesto, hashes e preservação histórica. Avisar em caso de divergência; não afrouxar gates para obter verde.

## Aceite

Testes offline cobrem parsing/pares, fila, transportes, clock, retenção, zero novos+retidos, promoção e baseline. A confirmação de produção depende de uma rodada oficial concluída e leitura do manifesto público. Não prometer cobertura100%, nem ausência permanente de mercados com base numa amostra de hoje.
