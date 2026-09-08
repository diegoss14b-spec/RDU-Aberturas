# Transição fechada da elegibilidade CLV — 08/09/2026

O portão antigo bloqueia corretamente uma queda de contagem, mas não distingue
perda do histórico de correção dos critérios. Reproduzido antes do patch:
`histórico VÁLIDO encolheu 335091→0 (>2%) sem migração aprovada`.
O novo relógio não pode inventar datas de observação para requalificar esse legado.

## O que permite a primeira publicação

Somente a transição de uma produção **sem política CLV** para a política exata
`observed-clock-strict-close/v1`: relógios observados verificáveis e fechamento
pré-jogo de no máximo 60 minutos.

A baseline é o snapshot Git `cef4b986941a2a6e9bedec1eb1947d4698fcf647`, que contém
o manifesto público do build `6cf6f3fc2beb4907acc5a2ed3bc79571` (02:24 BRT).
Não é o `code_sha` do manifesto: esse campo precede a persistência do snapshot.

`history_policy_baseline/baseline.json` vincula os bytes do manifesto, do histórico,
de cada arquivo fonte e do inventário `identities.json.gz` por SHA256. Inventário:
**835.382 IDs brutas e 346.572 IDs liquidadas**, incluindo arquivos quentes e arquivados.
Esses números abrangem todas as chaves, não apenas os mercados da tabela pública.

`build_history.py` compara os IDs antes da deduplicação de visualização. O deploy:

- verifica os hashes dos artefatos locais e o contrato de política no manifesto;
- lê o histórico público e exige correspondência exata com seu manifesto;
- exige o mesmo manifesto/histórico legado da baseline fechada;
- exige zero IDs brutas ou liquidadas ausentes, além de pisos de contagem;
- exige preservação dos totais públicos `monitoradas` e `liquidadas`;
- registra `CLV_POLICY_TRANSITION` no log, com hashes de origem/destino, build ID,
  política, contagens e hash do relatório de preservação.

Não existe aprovação por presença de arquivo. O antigo
`history_shrink_approved.json` não libera mais o portão. Outro snapshot legado,
ID removido/remapeado, baseline ausente, prova inválida ou fonte incoerente
**bloqueiam**, com motivo explícito. Não há remapeamento inferido.

## Publicações seguintes e limites

v1→v1 nunca usa a exceção de migração: continuam os pisos brutos/liquidados e a
trava de queda do CLV válido (tolerância já existente de 2%). Mesmo com CLV zero
nos dois lados, perda do banco continua bloqueada. A prova das IDs legadas é
revalidada em todas as publicações v1; erro na leitura da baseline não vira sucesso.

As IDs novas posteriores à baseline ainda são protegidas pelos totais e pelos
controles existentes de persistência, não por comparação individual contra um
inventário de cada publicação anterior. Não alegar essa garantia adicional sem
implantar inventários versionados por build.

O inventário não migra nem regrava nenhuma aposta. Resultados e relógios legados
continuam preservados e rotulados como não verificados. O indicador estrito pode
ficar inicialmente vazio; isso não significa que o histórico tenha sido apagado.

## Verificação

`test_history_policy_transition.py` cobre a transição válida e controles negativos:
substituição de ID com contagem igual, perda de liquidada, política desconhecida,
rollback, prova ausente/adulterada/reaproveitada, baseline pública diferente,
hash divergente, queda v1→v1, CLV zero com perda bruta e arquivo-bypass arbitrário.
O zero explícito também não pode cair no fallback antigo `head.n_valid`.

`python3 -m pytest test_*.py -q`: **337 passaram, 1 opcional pulado**.
Nada foi publicado por esta frente; revisão e publicação pertencem ao agente principal.
