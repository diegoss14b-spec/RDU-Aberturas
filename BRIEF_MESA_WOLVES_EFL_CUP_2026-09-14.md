# Mesa — Wolves na EFL Cup: correção segura de identidade

14/09/2026. Alteração autorizada pelo Diego. Este brief registra o código e os testes; a confirmação da publicação terá recibo separado. Não confundir código enviado com site atualizado.

## Para o Windows

- Repositório oficial: `diegoss14b-spec/RDU-Aberturas`, branch `main`.
- Sincronizar o código oficial antes de iniciar a próxima rotina. Preservar `canonical.py`, `test_identity_purity_wolves.py` e o novo `test_identity_purity_wolves_eflc.py`.
- Não restaurar `canonical.py` de cópias antigas do Desktop/Drive. O pacote é referência de fonte/testes, não substituição dos dados locais.
- A Mesa continua sendo publicada exclusivamente por `.github/workflows/valor.yml`; nunca publicar stub ou pasta local via Netlify.
- Não apagar histórico, remover o gate de identidade, aumentar tolerância fuzzy ou transformar Wolves em alias global para destravar a Mesa.
- Não iniciar rodadas manuais redundantes durante a recuperação. A execução anterior é preservada; um recibo posterior identificará a primeira publicação com esta correção.
- A correção de 12/09 que removeu o corte de 900 eventos da Superbet permanece intacta, assim como seus testes e a janela temporal.

## Causa comprovada

O último build público observado no diagnóstico era `c24f71ea8daf4b4596401cbec1fb4ba1`, gerado em 13/09 às 16h54min11s BRT. Captura, histórico, montagem e persistência continuavam funcionando. O gate interrompia a publicação antes do Netlify:

> identidade impura sofa:16992612 — everton x wolverhampton / everton x wolves

O mesmo motivo foi confirmado na primeira falha após esse build, execução `34781146714` (13/09 às 17h58 BRT), e na execução `34859660691` (14/09 às 12h33 BRT). Repetir a captura não resolve um bloqueio determinístico de identidade.

Não houve regressão de código entre o patch Superbet de 12/09 e o HEAD `7f9db307303efd6393909a64d4f601fd0c492c9e`: os avanços eram dados. A regra de Wolves de 11/09 estava preservada, porém restrita à Championship. Um novo confronto da EFL Cup expôs o caso ainda não contemplado.

## Evidência do evento

- Sofa `16992612`: Everton, time `48`, contra Wolverhampton, time `3`.
- Competição: EFL Cup, `league_id=21`, `label=EFLC`.
- Início: 16/09/2026 às 18h45 UTC / 15h45 BRT.
- 130 registros, todos com adversário e kickoff coerentes: 7k 36 (`Wolves`, `Inglaterra EFL Cup`); bet365 26 (`Wolverhampton`, `England EFL Cup`); EstrelaBet 44 (`Wolverhampton Wanderers`, `EFL Cup`); Sportingbet 24 (`Wolverhampton`, `EFL Cup`).

## Alteração implementada

O helper `fixture_scoped_alias_pair()` mantém o contrato Championship `18/ENG2` e adiciona um contrato separado EFL Cup `21/EFLC`. São aceitas apenas as grafias revisadas EFL Cup, England EFL Cup, English EFL Cup e Inglaterra EFL Cup, com a normalização de pontuação já existente.

A prova EFL exige:

1. IDs de evento, competição e times inteiros positivos na fixture; strings, booleanos e floats não servem como prova.
2. ID `3` no lado correto do Wolverhampton; adversário distinto, com nome exatamente correspondente à fixture atual.
3. Competição `21/EFLC`, mesmo dia BRT e diferença de kickoff de no máximo 45 minutos, conforme contrato existente.
4. Categorias preservadas: feminino, base, reservas e clubes homônimos não são incorporados.
5. Apenas um evento candidato; ao conferir um registro histórico, o evento deve coincidir com `expected_sofa_id` (o banco pode representar esse ID como string).

A grafia passa a ser comprovada pela fixture, sem confiar em nomes normalizados armazenados, confiança declarada ou nome de contexto alegado. Carabao Cup, League Cup e outras grafias ainda não revisadas não foram liberadas indiscriminadamente.

Não foram alterados odds, linhas, clocks, registros brutos, modelos, mercados habilitados, capturadores, gate, workflow, orçamento de captura nem regras de CLV. O ID do confronto não foi fixado no código: a prova serve para próximos adversários exatos do Wolverhampton nessa competição, desde que todos os requisitos sejam satisfeitos.

## Testes e replay

- 25 testes novos, com positivos do caso real e negativos de IDs, competição, categorias, adversários, horário, ambiguidade, cache, identidade esperada e não-mutação.
- Testes dos dois contratos Wolves: 49 aprovados e 205 subtestes aprovados.
- Suíte completa: 771 aprovados, 1 ignorado e 264 subtestes aprovados.
- Replay dos 20 shards quentes do HEAD acima: 329.954 chaves; 719 IDs do universo fixtures/board examinados. Apenas `16992612` muda de 2 grupos (94 + 36) para 1 grupo de 130; nenhum registro é reescrito ou perdido, nenhuma outra identidade muda e nenhuma identidade impura permanece nesse universo.
- Prova histórica existente schema3 validada: 1.009.491 identidades brutas, 346.572 liquidadas, 412 remapeamentos comprovados, zero ausentes. Essa leitura não equivale a reprocessar todo o arquivo histórico; o fluxo oficial recalcula e valida antes da publicação.

## Critério de conclusão

Só considerar publicado após workflow oficial com gate/deploy/smoke aprovados, manifesto público recente contendo a correção, cinco artefatos conferidos por hash/tamanho e preservação histórica validada. Captura de outra casa eventualmente degradada deve continuar visível como tal, sem carimbo de atualização fictício.

O recibo final será entregue na pasta de coordenação Mac↔Windows no Drive. Nenhuma automação nova foi criada por esta correção.
