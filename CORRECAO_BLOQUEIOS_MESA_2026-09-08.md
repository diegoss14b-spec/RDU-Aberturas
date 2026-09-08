# Correção dos bloqueios de publicação — 08/09/2026

Pedido do Diego: restaurar a atualização da Mesa, preservando os controles de identidade e o histórico. A versão pública estava congelada no build de 02:24 BRT. A presença deste documento no código não certifica publicação: exigir um ciclo completo, smoke e hashes dos artefatos públicos.

## Identidade: mesmo contrato na associação e na validação

`canonical.sofa_purity` agora reutiliza a equivalência contextual já verificada pelo associador. Não existe alias global permissivo para PSG. O nome abreviado só é aceito com competição revisada, os dois times corretos, data/horário compatível, uma única fixture e o mesmo ID Sofa que está sendo auditado. Nomes normalizados gravados anteriormente não são prova suficiente.

A ordem dos nomes crus não é invertida pela normalização. A semântica anterior da pureza de evento (o mesmo confronto pode aparecer em ordem invertida) foi preservada; não é autorização para inverter mercados de mandante/visitante.

Replay dos oito shards de setembro já auditados: 160.701 registros, 861 IDs Sofa. Somente a classificação de impureza do PSG mudou: 276 registros passaram de dois grupos para um. Outros jogos e categorias continuam protegidos. `test_identity_purity_aliases.py` cobre contextos ausentes/ambíguos, adversário incorreto, outro ID, data, competição, feminino/base/reservas e metadados falsos.

## Histórico: renomeação comprovada não é perda

A baseline original e seu inventário não foram substituídos. A interface de prova antiga continua comparando IDs literalmente. A nova prova aceita apenas o conjunto fechado de remapeamentos sustentado pelo certificado em `history_policy_baseline/remap_evidence_2026-09-08.json`, cujo hash é fixado em `history_policy_remap.py`.

O certificado é reproduzível a partir dos arquivos exatos do commit da baseline e do snapshot auditado. Não é uma lista genérica de aprovação. O builder verifica os registros atuais: destino exato, uma única origem direta, mesma casa/mercado/linha/lado, jogo/horário, abertura, extremos e observações preservados. Alteração não prevista bloqueia; nenhuma renomeação de ID liquidada da baseline é aprovada por esse mecanismo.

`raw_count` e `settled_count` continuam sendo contagens de registros reais. IDs antigas não são adicionadas artificialmente para aumentar o banco. A prova distingue ausências literais, renomeações validadas e perdas não explicadas; se uma ID antiga reaparecer literalmente, ela não é contada novamente como renomeada. Os pisos do banco público continuam obrigatórios.

Os comprovantes são copiados antes da deduplicação da visualização. Mudanças posteriores de objetos em memória não podem alterar a prova já calculada. Testes adversariais em `test_history_policy_remap.py` verificam perdas, falsas origens, destinos reutilizados, preços/linhas alterados, certificado errado e alteração da prova.

Não apagar o certificado, trocar a baseline, contar qualquer `merged_from_keys` como presença, desligar o gate ou inventar horários para liberar o deploy. Uma futura renomeação fora desse conjunto requer nova evidência e revisão.

## Publicação e continuidade

Publicar somente pelo workflow oficial `valor.yml`; não usar a pasta local do RDU Stats nem seu publicador. Nenhum peso de modelo, regra de aposta, segredo, tarefa Windows, mercado habilitado ou limite de proteção foi alterado nesta correção.

Conferir após o ciclo: teste Python aprovado, gate aprovado, persistência confirmada, Netlify pronto, smoke aprovado e manifesto público coerente. CLV estrito pode inicialmente ter amostra pequena ou zero: reclassificar elegibilidade não apaga o histórico legado.

O problema parcial de paginação do calendário FAC se recuperou na fonte durante a investigação (67 eventos/1.103 fixtures no ciclo das 12:13 BRT). O limite de idade do Sofa e a proteção de captura parcial não foram relaxados.
