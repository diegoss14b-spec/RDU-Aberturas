# Mesa — correção contextual Wolves × West Brom — 18/09/2026

## Status e coordenação Mac ↔ Windows

Correção autorizada por Diego, validada localmente e destinada ao repositório oficial RDU-Aberturas. Este brief acompanha o código; NÃO significa, sozinho, que houve publicação. O recibo final informará o run, commit, build publicado e verificações efetivas.

Windows: sincronizar a fonte oficial antes de trabalhar na Mesa, preservando esta correção e seus testes. Não sobrescrever canonical.py com cópias anteriores. Não iniciar deploy local, não substituir bases e não cancelar rodadas por causa deste brief. A publicação pertence exclusivamente ao workflow oficial valor.yml, com seus gates intactos.

## Incidente e causa comprovada

A última publicação bem-sucedida era de 18/09 às 12:53:58 BRT, build 05162c5b7d414045ae4e9bf96760030a. As capturas e o histórico continuaram, mas a validação de identidade bloqueou os deploys seguintes. Primeira falha observada: run 35366159352, gate às 13:35:50 BRT. Última falha examinada no diagnóstico: 35414527115. Foram 15 falhas e 3 cancelamentos, sem nova publicação nesse intervalo.

Erro: sofa:16391693 aparecia como dois confrontos, `wolverhampton x west brom` e `wolves x west brom`. É o mesmo jogo: Wolverhampton (Sofa team ID 3) × West Bromwich Albion (ID 8), Championship (liga 18 / ENG2), 20/09/2026 às 11:00 UTC / 08:00 BRT.

A regra anterior provava Wolves apenas se o outro clube tivesse o nome idêntico ao da fixture. O adversário também vinha abreviado, portanto a prova falhava. A correção da EFL Cup de 14/09 continuava intacta: NÃO houve rollback; não era uma falta de suporte à Championship, nem problema de cache do navegador.

## Mudança implementada

Arquivo de produção: canonical.py, função fixture_scoped_alias_pair.

- Prova conjunta restrita ao par de clubes IDs 3 e 8 nas competições inglesas já revisadas: Championship (18/ENG2) e EFL Cup (21/EFLC).
- Formas revisadas: Wolves/Wolverhampton e West Brom/West Bromwich Albion. Todas as combinações normalizam juntas, evitando pares intermediários.
- Exige IDs de evento, liga e ambos os clubes como inteiros positivos; nomes completos normalizados na fixture; liga e label exatos; mesmo dia BRT; kickoff dentro de 45 minutos; categorias compatíveis; evento único e, quando informado, mesmo expected_sofa_id.
- Preserva orientação legítima, inclusive quando um provedor apresenta o par invertido, conforme semântica existente.
- Se a prova deste par falhar, retorna sem alias e NÃO recorre à regra antiga de um único clube.
- Não adiciona aliases globais, não faz fuzzy matching mais permissivo e não codifica o número desta partida na regra de produção.

Não foram alterados odds, mercados, capturadores, modelos, orçamento de APIs, gates, workflow, publicador, relógios, dados de histórico ou frontend. Não apaga nem funde registros de apostas; somente reconhece a identidade contextual da partida.

## Evidências e testes

- Nova suíte test_identity_purity_wolves_west_brom.py: 23 testes e 204 subtestes, incluindo reversões, tipos/IDs incorretos, feminino/base/reservas, ligas homônimas, limites de horário, eventos ambíguos, não mutação e cache.
- Três suítes Wolves: 72 testes e 409 subtestes aprovados.
- Suíte completa: 794 testes aprovados, 1 ignorado, 468 subtestes aprovados.
- Revisão independente do guard e git diff --check aprovados.
- Replay real: 440.289 keys inventariadas em 27 shards; 65.030 relevantes ao universo de 657 fixtures, 396 IDs com histórico. Apenas 16391693 mudou: dois grupos 132 + 22 viraram um grupo, preservando todas as 154 keys. Nenhuma outra identidade mudou, nenhum registro sumiu. Repetição determinística.
- Limitação do replay: fixture/board publicados (657 fixtures); o snapshot de 604 fixtures da rodada bloqueada não foi persistido. A nova rodada oficial precisa validar a captura recém-gerada.
- SHA256 do canonical.py corrigido: 2e652b82548bf6f4f3891b1b3ffc8fbf2d3d7ddccb075c0deef931fe16b0e5da.

## Publicação e aceite

Usar somente GitHub Actions valor.yml, sem concorrência destrutiva e sem desativar o gate. Não considerar concluído apenas porque o push passou.

Após o run: comprovar testes, gate, manifesto, persistência, Netlify e smoke; identificar o SHA realmente utilizado no checkout; comparar os cinco artefatos públicos com seus hashes e o snapshot persistido; verificar fonte publicada, horários recentes, preservação schema 3 do histórico e contadores não reduzidos. Enquanto o jogo for futuro, exigir a fixture correta e uma única ocorrência no board, por ID e pelos aliases no mesmo kickoff. Conferir também a página autenticada.

Baseline público antes desta mudança: 667.903 monitoradas e 346.558 liquidadas. O recibo final separado registrará o resultado real da publicação, inclusive eventuais limitações por casa.

## Limitações independentes — não confundir com esta correção

A bet365 vinha com captura parcial por rede/orçamento e a Pinnacle utilizava feed preservado. Este patch não corrige essas limitações e não autoriza carimbar odds antigas como novas. Os avisos de frescor por casa e as proteções de histórico devem permanecer.
