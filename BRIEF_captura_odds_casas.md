# BRIEF — Como capturar odds de casas de aposta (do zero)

**Público:** um Claude (ou dev) que entende de código mas nunca fez scraping de odds.
**Objetivo:** ensinar o **método geral** que serve pra qualquer casa nova + dar um **catálogo**
das casas que o RDU já resolveu, como exemplos concretos.
**Fonte deste brief:** os `fetch_odds_*.py` do `valor-app/` (código em produção da Mesa de
Aberturas) + a API da Sportingbet decifrada e testada ao vivo em 24/07/2026.

> Regra de ouro: **só leitura (GET), ritmo educado, cache do que já pegou.** Nunca fazer login,
> nunca mandar aposta, nunca martelar o servidor. A gente lê o cardápio de odds público, nada mais.

---

## PARTE 1 — MÉTODO GERAL

Esta parte é o que importa. Uma casa nova cai em um destes 7 passos. Faça na ordem.

### 1. Farejar a rede (achar o endpoint que traz as odds)

Toda casa moderna é um SPA (site em JavaScript) que busca as odds de uma **API JSON** por baixo.
Você não faz scraping do HTML — você chama a mesma API que o site chama.

Como achar:
1. Abra a casa no navegador (Chrome).
2. **DevTools (F12) → aba Network → filtro XHR/Fetch.**
3. Digite no campo de filtro algo como `api`, `odds`, `fixture`, `offer`, `event`, `market`.
4. **Clique num jogo** e observe qual request aparece trazendo um JSON gordo com os mercados.
5. Botão direito no request → **Copy → Copy as cURL**. Esse cURL tem a URL, os headers e a query
   string exata. É o seu ponto de partida.

Você vai reconhecer **dois tipos de request** (veja passo 3): um que lista muitos jogos de uma vez,
e um que traz todos os mercados de um jogo só. Guarde os dois.

### 2. O muro anti-bot: é o **fingerprint TLS**, não o header

Aqui está o pulo do gato que a maioria erra. Quando você copia o cURL e roda no terminal, ou faz
`requests.get(...)` em Python, **a casa bloqueia** — normalmente 403, às vezes uma página de erro
de alguns KB. O instinto é "faltou algum header". **Quase sempre não é.**

O bloqueio é por **fingerprint TLS**: o servidor olha o *handshake* da conexão (ordem das cifras,
extensões TLS) e vê que não é um Chrome de verdade — `curl` e a lib `requests` têm assinatura de
robô. Nenhum header conserta isso.

**A chave é `curl_cffi` com `impersonate="chrome124"`** — ela imita o handshake TLS do Chrome 124.
Mesma URL, mesmos headers, só que o handshake passa por navegador. Resultado:

```python
from curl_cffi import requests as creq
r = creq.get(url, impersonate="chrome124", timeout=25)
```

Antes/depois medido na Sportingbet, direto do Brasil, mesma URL (24/07/2026):

| Cliente | Resultado |
|---|---|
| `curl` puro (terminal) | **HTTP 403**, página de bloqueio (~17 KB) |
| `curl_cffi impersonate="chrome124"` | **HTTP 200**, 9 MB de JSON, 400 jogos |

Regras práticas:
- **Sempre comece pelo `curl_cffi`.** Se der 200, acabou — não precisa de proxy nem browser.
- Alguns casos ainda pedem **headers de identidade do site** (`Origin`/`Referer` do domínio da
  casa, `User-Agent` de browser). Betfast e Superbet exigem; sem eles → 403 mesmo com o TLS certo.
  Se o `curl_cffi` sozinho não passar, adicione esses headers antes de desistir.
- **Último recurso — Playwright** (navegador headless de verdade): só quando a casa tem um
  *challenge* de JavaScript que roda no cliente pra liberar um token (ex.: o 7k, que precisa de
  JWTs anônimos gerados na página). Playwright é lento, frágil e dá manutenção — evite; use HTTP
  puro sempre que der.

### 3. O padrão universal: **lista + detalhe**

Quase toda casa organiza a API em dois endpoints:

- **LISTA** (barato, 1 request): traz muitos jogos de uma vez — id, times, horário, liga, e às
  vezes uns poucos mercados principais. Você chama **uma vez** e descobre o que tem no cardápio.
- **DETALHE** (caro, 1 request por jogo): você passa o **id de um jogo** e recebe **todos** os
  mercados dele (escanteios, cartões, faltas, chutes...). É onde estão as odds que a gente quer.

Fluxo: chama a lista → filtra os jogos que interessam (janela de horário, liga, nº de mercados) →
para cada jogo, chama o detalhe. Como o detalhe é 1 request por jogo e são dezenas de jogos, rode
os detalhes **em paralelo com um ThreadPool de ~8 workers** (é o que EstrelaBet e outros usam).
Não mais que isso — 8 é educado e já é rápido.

### 4. Auth: quase sempre é público

A boa notícia: **a maioria das casas não exige login**. O que elas usam é um **token/accessId
público**, embutido no próprio JavaScript da página — qualquer visitante manda o mesmo. Onde achar:

- Está na **query string** dos requests que você fareja no passo 1 (ex.: `x-bwin-accessid=...` na
  Sportingbet, `integration=estrelabet` na EstrelaBet), ou num header.
- Se um dia parar de funcionar, **fareje de novo**: abra a home no navegador e leia qualquer
  request `/api` — o token novo está lá.

Esse tipo de token é **público por natureza** (vai pra dentro do navegador de todo mundo), então
pode ficar no código. A exceção é a **bet365**, que não tem API pública aberta — o acesso é via um
provedor terceiro pago (**BetsAPI**), cujo token é privado. Esse **nunca** vai em código: fica em
config `gitignored` ou variável de ambiente (ver Parte 2).

### 5. Geo-block: casas `.bet.br` só respondem do Brasil

As casas reguladas no Brasil (domínio `.bet.br`) **bloqueiam IP estrangeiro**. Se você roda de um
servidor/nuvem fora do BR (ou de datacenter que a casa reconhece), leva bloqueio geográfico.

Duas saídas:
- **Rodar do Brasil** (máquina local no BR, ou servidor BR): dispensa proxy — **economia real**, é
  o modo mais barato e rápido.
- **Proxy residencial BR** quando você precisa rodar na nuvem. No RDU isso é o `br_proxies()` do
  `capture_common.py`, que lê credenciais de um **config gitignored** (nunca em código). Local =
  `None` (direto); nuvem = proxy BR.

Atenção: algumas plataformas rate-limitam **IP de datacenter** mesmo dentro do BR (a Altenar/
EstrelaBet cortava a captura pela metade da nuvem, mas do IP residencial BR ia 100%). Se a captura
vier truncada só na nuvem, é isso — troca pro proxy residencial.

### 6. Normalização: o trabalho chato de verdade

Cada casa nomeia os mercados de um jeito. "Total de Escanteios" numa é "Escanteios" na outra é
"Corners" na terceira. Pra comparar odds entre casas você precisa de um **mapa canônico**: um
dicionário `nome_da_casa → nome_padrão_do_board`. Exemplos reais dos scripts do RDU:

```
"total de cartões"   → "Cartões"
"total de faltas"    → "Faltas"
"total de escanteios"→ "Escanteios"
"total de chutes"    → "Finalizações"
"total de chutes no gol" → "Chutes no gol"
```

Duas armadilhas:
- **Parsear a linha O/U.** As opções vêm como texto "Mais de 8.5" / "Menos de 8.5". Extraia o
  número (a linha) e o lado (over/under) com regex `mais`/`menos` (ou o campo estruturado quando
  existe, ex.: `optionTypes:["Over"]` na Sportingbet). Junte over+under **da mesma linha** num par;
  só grave pares completos.
- **Excluir o que não é O/U de total de jogo.** As casas misturam mercados de **jogador** ("N
  chutes do fulano"), **por tempo** ("1º tempo"), **handicap**, **par/ímpar**, **placar exato**,
  **"qual time faz mais"** (prop binária, não O/U). Tudo isso tem que cair numa *denylist*. O RDU
  filtra por palavras: `jogador`, `1º tempo`, `handicap`, `ímpar`, `exato`, `ambas`, etc.

**O problema mais chato de todos é casar o mesmo jogo entre casas.** "Vélez Sarsfield" numa casa é
"Velez" na outra, e os ids são internos de cada uma. A solução é **matching fuzzy por nome + o
horário de início** (kickoff quase idêntico entre casas). Não existe bala de prata — é o que mais
dá trabalho e onde mais entram bugs. Reserve tempo pra isso e valide manualmente alguns jogos.

### 7. Ética e robustez

- **`sleep` entre requests** (0,5–0,8 s é o padrão do RDU) — não martele.
- **Cache do que já pegou.** Jogo encerrado não muda de odd; não re-baixe. (No RDU o cache de
  feeds encerrados é "sagrado".)
- **Respeite rate-limit.** Se a casa começar a cortar, diminua workers e aumente o sleep.
- **Tolerância a falha:** retry com backoff (3–4 tentativas), timeout curto, e um **piso mínimo de
  eventos** — se a captura veio muito abaixo do esperado, trate como falha (exit ≠ 0) em vez de
  publicar dado ruim. Todo script do RDU tem um `MIN_EVENTS`.
- **Só GET.** Nunca poste, nunca autentique, nunca clique em "apostar".

---

## PARTE 2 — CATÁLOGO (as casas que o RDU já resolveu)

Uma mini-ficha por casa. Onde diz "ver `fetch_odds_X.py`", o detalhe fino está no cabeçalho do
script (todos comentados). **Endpoints só listados aqui foram confirmados no código/ao vivo — não
invente variações.**

---

### Sportingbet — *(decifrada e testada ao vivo em 24/07/2026)*

- **Plataforma:** Entain/bwin (mesmo motor da bwin / Betboo). API `cds-api`.
- **Base:** `https://www.sportingbet.bet.br/cds-api`
- **Auth:** query string `x-bwin-accessid=YTRhMjczYjctNTBlNy00MWZlLTliMGMtMWNkOWQxMThmZTI2`
  (público, embutido no JS da página; se mudar, fareje de novo lendo qualquer request `/cds-api`
  na home). **Sem cookie, sem login.**
- **Anti-bot:** só o fingerprint TLS do `curl_cffi impersonate="chrome124"` basta. `curl` puro
  toma 403.
- **Lista** (1 request ≈ 945 jogos possíveis; medido: 400/página, 9 MB):
  ```
  GET /cds-api/bettingoffer/fixtures?x-bwin-accessid={AID}&lang=pt-br&country=BR&userCountry=BR
      &fixtureTypes=Standard&state=Latest&offerMapping=Filtered&offerCategories=Gridable
      &sportIds=4&skip=0&take=400&sortBy=StartDate
  ```
  `resp.fixtures[]`: cada `f` tem `id` (ex.: `"2:7828587"`), `name.value`
  (`"Vélez Sarsfield - Instituto AC Cordoba"`), `startDate` (ISO), `competition.name.value`
  (a liga; pode vir em `tournament`), e `optionMarkets[]` (só os mercados do grid — nº alto = jogo
  com oferta rica).
- **Detalhe** (todos os mercados de 1 jogo; medido: 78 mercados, 178 KB):
  ```
  GET /cds-api/bettingoffer/fixture-offers?x-bwin-accessid={AID}&lang=pt-br&country=BR
      &userCountry=BR&fixtureIds={fid}&offerMapping=All
  ```
  Estrutura **aninhada** — ache os mercados varrendo recursivo por objetos que têm `options` **E**
  `name.value`. Cada mercado: `{name:{value:"Total de Escanteios"}, options:[...]}`. Cada opção:
  `{name:{value:"Mais de 8.5"}, price:{odds:5.5}, parameters:{optionTypes:["Over"]}}`.
- **Mercados-alvo (medidos em jogo real):**
  - `"Total de Escanteios"` → O/U de jogo inteiro (`Mais de 8.5`/`Menos de 8.5`, `optionTypes`
    `["Over"]`/`["Under"]`, odd em `price.odds`). **É o mercado principal pra Mesa.**
  - `"{Time} - Total de Escanteios"` → escanteios por time (mesmo formato O/U).
  - **Cartões** (perto do kickoff): `"Ambas as equipes recebem N ou mais cartões"` (Sim/Não — prop
    binária, **não** é O/U) e `"Mais cartões"` (qual time). Capturar como vier, não forçar em O/U.
  - **Chutes / chutes no gol:** por **jogador** ("N ou mais chutes" → lista de jogadores), não
    total de time.
  - ⚠️ Nem todo jogo tem cartões/chutes — abrem **na proximidade do kickoff** (igual Betfast).
    Escanteios quase sempre têm.
- **Geo:** `.bet.br` → rodar do Brasil ou proxy residencial BR.

---

### Betano — ver `fetch_odds_betano.py`

- **Plataforma:** API JSON pública do site `.bet.br`.
- **Base:** `https://www.betano.bet.br`
- **Auth:** pública, sem login. Anti-bot: `curl_cffi impersonate="chrome124"`.
- **Lista:** `/api/sport/futebol/jogos-de-hoje/`
- **Detalhe:** por evento, dividido em **abas** por tipo de mercado (`bt=1` gols · `bt=4`
  escanteios · `bt=5` cartões · `bt=6` estatísticas = chutes/chutes no gol/faltas/impedimentos/
  tiros de meta). Mercados O/U em `selections[]` com `handicap` (linha), `fullName`
  ("Mais.../Menos..."), `price` (odd).
- **Pegadinha:** os mercados dos nossos modelos ficam espalhados nas abas `bt` — precisa buscar as
  4. Geo `.bet.br` (Brasil/proxy BR).

---

### 7k — ver `fetch_odds_7k.py`

- **Plataforma:** FSSB ("pulse"). `.bet.br`.
- **Auth:** **JWTs anônimos** (`internalToken`/`sessionToken`) que vêm inline no HTML da *launch
  page* do FSSB (via `7k.bet.br/api/sports/anonymous-launch`). Expiram em ~1 dia → pegar frescos a
  cada run. **Playwright é fallback** se o HTTP puro falhar em pegar os tokens (caso de challenge
  de JS).
- **Lista:** `/api/pulse/snapshot/events?lang=BR-PT` → filtra futebol + prematch + muitos mercados.
- **Detalhe:** `markets/all?markets=<eid>:ALL` descobre os `MarketType._id` de estatística; depois
  `markets/all?markets=<eid>:<codes>` traz as `Selections` com preço (`Points`=linha,
  `Name`=Mais/Menos, `DisplayOdds.Decimal`=odd).
- **Pegadinha:** mercado de estatística só existe em jogos com **141+ mercados totais** — filtrar
  por `MIN_MARKETS=60` e não esperar mais que ~7–14 jogos/run (é o tamanho real da oferta). Geo
  `.bet.br`.

---

### EstrelaBet — ver `fetch_odds_estrelabet.py`

- **Plataforma:** **Altenar** (`sb2frontend-altenar2.biahosted.com`). JSON limpo, **sem auth**.
- **Base:** `https://sb2frontend-altenar2.biahosted.com/api/widget`
- **Lista:** `GetEvents?sportId=66&hoursRange=N` → eventos (`id`, `name`, `startDate`, `champId`) +
  campeonatos.
- **Detalhe:** `GetEventDetails?eventId=<id>` → `markets`/`childMarkets` + `odds[]` (`name`,
  `line`, `price`).
- **Mercados de jogo inteiro:** nomes limpos tipo `"Total cartões"`, `"Total de Faltas"`,
  `"Totais chutes"`, `"Total de Escanteios"`; odd `name` = `"Mais de X"`/`"Menos de X"`. Exclui
  jogador/técnico/tempo/handicap/exatos/por-time.
- **Pegadinha:** a Altenar **rate-limita IP de datacenter** (nuvem pega ~5 detalhes e é cortada) —
  do IP residencial BR vai 100%. Use proxy BR na nuvem; local vai direto. Detalhes em paralelo
  (`WORKERS=8`). **É o script mais parecido com o padrão lista+detalhe de plataforma de terceiro —
  use como molde.**

---

### Superbet — ver `fetch_odds_superbet.py`

- **Plataforma:** API pública "offer" servida por **Fastly** (CDN).
- **Base:** `https://production-superbet-offer-br.freetls.fastly.net/v2/pt-BR`
- **Auth:** pública, sem login.
- **Lista:** `/events/by-date?currentStatus=active&offerState=prematch&sportId=5&startDate&endDate`
- **Detalhe:** `/events/{eventId}` → campo `odds[]` (`marketName`, `name`, `price`). `struct` →
  nomes de torneio/categoria.
- **Pegadinha:** o CDN às vezes manda `content-encoding: gzip` **mentiroso** → ler os **bytes crus**
  e tentar `gzip.decompress`, caindo pra texto puro se falhar. Headers `Origin`/`Referer` do
  domínio superbet são obrigatórios. `marketName` limpo (`"Total de Cartões"`), outcome
  `"Mais de X.5"`/`"Menos de X.5"`. Geo `.bet.br`.

---

### Pinnacle — ver `fetch_odds_pinnacle.py`

- **Plataforma:** API **guest Arcadia** (`guest.api.arcadia.pinnacle.com` — internacional, **não** é
  `pinnacle.bet.br`). Só cobre **Escanteios (Corners) + Cartões (Bookings)**.
- **Base:** `https://guest.api.arcadia.pinnacle.com/0.1` · esporte 29 (Soccer).
- **Auth:** guest, pública. Headers `Origin`/`Referer` de `pinnacle.com`.
- **Lista:** `/sports/29/matchups?withSpecials=true`. Os specials de volume são *matchups filhos*
  (`type=matchup`, `parentId` = jogo principal) com `units='Corners'` ou `units='Bookings'`.
  ⚠️ `type=special`+`units=Bookings` = props de **jogador** (ignorar).
- **Detalhe (odds):** `/matchups/{id}/markets/straight` (period 0 = FT). Chaves: `s;0;ou;{linha}`
  (total do jogo), `s;0;tt;{linha};home`/`;away` (por time).
- **Pegadinha:** preços vêm em **formato americano** (±100) → converter pra decimal. Linhas de
  Bookings ~2,5–4,5 = contagem de cartões (não confundir com "booking points" 10/25). Não é
  `.bet.br` (API gringa) — sem geo-block BR.

---

### Betfast — ver `fetch_odds_betfast.py`

- **Plataforma:** **BetConstruct** ("sportsbookv4", brand/clientID 99), em iframe. Feed REST público.
- **Base (host):** `https://analytics-sp.googleserv.tech`
- **Auth:** **sem login e sem proxy** (IP de datacenter serve). Mas os headers `Origin`/`Referer`
  de `betfast.bet.br` + `User-Agent` de browser são **obrigatórios** (sem eles → 403).
- **Lista/árvore:** `/api/sport/getheader/pt` (Sports→Regions→Champs→GameSmallItems);
  `/api/sport/getheader/teams/pt` (nomes dos times); `/api/prematch/getprematchmarketsbysport/pt/,1,`
  (catálogo — posições Over/Under por mercado).
- **Detalhe:** `/api/prematch/getprematchgamefull/99/{gid}` (~300 KB) →
  `game.ev{marketId:{oddId:{pos,coef,...}}}`. O resumido `getprematchgameall` **não** traz os
  especiais — precisa do `full` por jogo. Mercados por `marketId` numérico (ex.: `536`=Cartões,
  `1790`=Faltas, `531`=Escanteios; ⚠️ **não** usar `535` = booking *points*).
- **Pegadinha operacional:** a Betfast **só abre os mercados especiais no DIA do jogo, de manhã**.
  Em dia vazio o fetcher sai com 0 eventos **sem erro** (`MIN_EFF=0`) — normal. Revalidar de manhã
  em dia de jogo.

---

### bet365 — ver `fetch_odds_bet365.py`

- **Exceção do catálogo:** bet365 **não tem API pública aberta**. O acesso é via provedor terceiro
  **pago — BetsAPI** (`api.b365api.com`).
- **Auth:** token **privado** da BetsAPI. **NUNCA em código** — fica em env (`BETSAPI_TOKEN`, secret
  do CI) com fallback em config local `gitignored`. O repo é público, o token jamais vai em commit.
- **Lista:** `GET /v1/bet365/upcoming?sport_id=1&token=&page=` (50/página; poluído de Esoccer/SRL —
  filtrar por nome de liga).
- **Detalhe:** `GET /v3/bet365/prematch?token=&FI=a,b,c` (até ~10 FIs por chamada). `sp.{mercado}.
  odds[]` = `{header:'Over'|'Under'|'1'|'2', name:'5.5', odds:'2.000', handicap?}`.
- **Pegadinhas:** a resposta tem **seções-dicionário** (main/corners/cards_fouls/...) **E** uma
  lista `others` (~79 blocos) — o parser precisa varrer as duas. Muito mercado só existe na lista.
  **Total de chutes do jogo a BetsAPI não entrega** (`match_shots` vem sempre `odds:[]`); dá pra ter
  chutes no gol e por-time. Consumo controlado porque o token é compartilhado (gate de full a cada
  ~3h). Detalhes finos no cabeçalho do script.

---

## Apêndice — infra compartilhada do RDU (`capture_common.py`)

Os scripts do `valor-app` compartilham utilitários que vale reusar numa casa nova:
- `br_proxies()` — proxy residencial BR (config gitignored) na nuvem; `None` (direto) local.
- `odds_window()` / `in_window()` — modo "close" que estreita a janela pra jogos iminentes.
- `finish()` / `write_odds_latest()` — grava a saída no formato normalizado do board
  (`data/odds/{casa}_{stamp}.jsonl` + ponteiro `{casa}_latest.json`) e aplica o `MIN_EVENTS`.

Formato de saída padrão (o "board"): 1 linha JSONL por jogo, com `mercados` (jogo inteiro) e
`mercados_time` (por mandante/visitante), casa identificada. Uma casa nova só precisa produzir esse
mesmo formato pra entrar na Mesa de Aberturas.
