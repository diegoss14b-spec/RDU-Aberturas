# -*- coding: utf-8 -*-
"""mesa_shared.py — contrato COMPARTILHADO Mesa (build_board) × mesa_bot (juiz).

Nasceu no review de 19/08/2026 (brief Mesa×Modelos): o juiz do mesa_bot tinha
uma CÓPIA espelhada de FIXTURE_LABEL_COMP/limiares/three_way/de_vig e cópia
espelhada driftaria em silêncio — flag da Mesa e sinal do bot divergindo sem
ninguém saber por quê. Agora a régua mora AQUI e os dois importam.

Regras:
- PURO: sem import de build_board/value_pricers/fetch_* (o juiz roda no Actions
  com checkout mínimo; side-effects aqui quebrariam o bot).
- Mudou limiar/mapa? É mudança de PRODUTO (afeta board E bot juntos) — anotar no
  commit e conferir a paridade (audit_mesa_bot_parity.py) depois.
"""

# MARGIN_MIN: margem implícita negativa = par promocional/incompatível (brief §4)
EV_MIN, EDGE_MIN = 0.05, 0.04
MARGIN_MIN, MARGIN_CAP = 0.0, 0.12
P_LO, P_HI = 0.15, 0.85  # P∈[15,85]% = região calibrada (evita artefato longe do μ)

LEAGUE_FORA = (
    # feminino / base / reservas (mesmos nomes de clube do masculino profissional)
    "femin", "frauen", "women", "ladies", "femenil", "femenin",
    "sub-15", "sub-17", "sub-20", "sub 20", "sub-23", "u17", "u19", "u20", "u21", "u23",
    "juvenil", "junior", "youth", "reserv",
    # mata-mata nacional (times da 1ª divisão jogando fora do regime da liga)
    "copa", "cup", "taca", "pokal", "beker", "trophy", "supercopa", "supercup",
    # vizinhos de divisão com times rebaixados no bundle
    "1. div", "1.div", "1st division", "obos",                 # Noruega 2ª
    "hypermotion", "smartbank", "laliga 2", "la liga 2",       # Espanha 2ª
    "segunda", "primera b", "primera federac", "rfef",         # ES/latam 2ª-3ª
    "serie c", "serie d", "2. liga", "liga 3", "league one", "league two",
    # 2ª/3ª divisão norueguesa (02/08): "1. div" já estava, mas o board de hoje
    # trazia "Noruega - 2.Division Gr.1/Gr.2" e "3.Division Gr.1/Gr.5" — 7 jogos
    # entrando como Eliteserien, com times B e rebaixados que EXISTEM no bundle.
    "2. div", "2.div", "3. div", "3.div", "2nd division", "3rd division",
    "divisjon", "division 2", "division 3",
    # feminino abreviado: "Brasil - Brasileiro - Série A (F)" da Superbet estava
    # (e está, no origin) classificando como BR-B — "femin" não pega "(f)".
    "(f)", " f)", "(fem",
)

# rótulo canônico da fixture da Sofascore (TOURNAMENTS do fetch_fixtures_sofascore)
# → código de liga do bundle dos modelos. É a IDENTIDADE CERTA: a fixture traz o
# id do torneio e os ids REAIS dos dois times, então não há por que adivinhar liga
# por texto livre da casa nem time por fuzzy de nome. Rótulo ausente daqui = sem
# modelo (fail-closed) — é o caso de copa/continental (WC, UCL, UEL, BR-CdB,
# Libertadores, Sudamericana), que não têm modelo mesmo.
FIXTURE_LABEL_COMP = {
    "BR-A": "BR-A", "BR-B": "BR-B", "EPL": "PL", "LaLiga": "LL", "SerieA": "SA",
    "Bundesliga": "BU", "Ligue1": "L1", "MLS": "MLS", "Argentina": "ARG",
    "ARG2": "ARG2", "CSL": "CHN", "Allsvenskan": "SWE", "Uruguay": "URU",
    "Ecuador": "ECU", "Russia": "RUS", "Eliteserien": "NOR", "LigaMX": "MEX",
    "MEXE": "MEXE", "BOL": "BOL", "CHI": "CHI", "PER": "PER", "COL": "COL",
    "COL2": "COL2", "CZE": "CZE", "ROU": "ROU", "DEN": "DEN", "SUI": "SUI",
    # ⚠ NÃO mapear "CDA": na Sofa o label CDA é a Canadian Premier League
    # (tid 13470 em fetch_fixtures_sofascore.py), mas o comp "CDA" do bundle é a
    # COPA ARGENTINA (35 clubes argentinos; 29 também no comp ARG). Colisão de
    # sigla, não equivalência — mapear publicaria identidade falsa. O mesmo engano
    # está no LABEL2COMP do build_model_ledger.py e vale corrigir lá também.
    #
    # ── 05/08/2026: as 29 ligas novas do fetcher. SEM esta linha o trabalho de
    # buscar o fixture não vale nada — o jogo ganha sofa_id e mesmo assim fica
    # sem preço, porque é DAQUI que sai o comp do bundle. Metade feita seria
    # exatamente o tipo de passo que "roda sem erro" e não produz efeito.
    #
    # ⚠ Cada rótulo abaixo foi conferido contra a competição REAL do tid (nome +
    # país + temporada ativa), justamente pra não repetir a colisão do CDA acima:
    # sigla igual não é competição igual. A verificação pegou dois enganos meus
    # (Grécia U19 no lugar da principal; Sérvia numa entidade de 3 temporadas).
    #
    # ⚠ "ECL" (Conference League) NÃO entra — DECIDIDO pelo Diego em 05/08, com
    # medição, e não é pra revisitar sem dado novo.
    #
    # O caso parecia forte: 2º maior bloco sem casamento no board (27 jogos) e o
    # bundle EXISTE (147 times). E a justificativa escrita na exclusão de
    # UCL/UEL logo acima ("não têm modelo mesmo") está mesmo DESATUALIZADA — os
    # bundles de 2026-08-04 trazem CL 141, EL 141 e ECL 147 times.
    #
    # O que decidiu foi a COBERTURA DE STATS da base, medida em 05/08:
    #     ECL  49,8%  ← metade dos jogos sem estatística
    #     CL   75,3%   ·  EL 75,5%
    #     LIB 100,0%   ·  SUD 100,0%
    #     ligas domésticas (PL/LL/SA/BU/L1/BR-A) ~100%
    # Precificar a Conference é construir preço sobre meia base. E a captura de
    # lacunas da mesma data confirmou que o buraco NÃO é recuperável: dos 541
    # core-nulls que a Sofa devolveu sem stat nenhuma, 384 eram ECL e 108 CL —
    # 91% dos vazios. A fonte não tem.
    #
    # Ironia registrada porque importa pra próxima decisão: LIB e SUD têm 100%
    # de cobertura e estão excluídas, enquanto a ECL a 50% era a candidata. Se
    # um dia quiserem continental precificada, o caminho honesto é LIB/SUD.
    #
    # O torneio SEGUE no fetcher: o fixture dá identidade correta e evita o
    # casamento no evento errado (caso Sporting de 22/07). É só o PREÇO que fica
    # de fora.
    "EFLC": "EFLC", "NED": "NED", "GER2": "GER2",
    "SCO": "SCO", "ENG2": "ENG2", "BEL": "BEL", "POR": "POR",
    "ITA2": "ITA2", "JPN": "JPN", "AUT": "AUT", "POL": "POL",
    "TUR": "TUR", "GRE": "GRE", "SAU": "SAU", "UKR": "UKR",
    "CRO": "CRO", "SRB": "SRB", "BUL": "BUL", "VEN": "VEN",
    "PAR": "PAR", "AUS": "AUS", "ESP2": "ESP2", "FRA2": "FRA2",
    "CDF": "CDF", "CDI": "CDI", "CDR": "CDR", "DFB": "DFB", "FAC": "FAC",
}


def de_vig(over, under):
    if not over or not under or over <= 1 or under <= 1: return None
    po, pu = 1 / over, 1 / under; tot = po + pu
    return {"p_over": po / tot, "p_under": pu / tot, "margin": tot - 1}


def three_way(ln_):
    """A linha vem de um mercado de TRÊS VIAS (over / exato / under)?

    Na 7k existe o OU621 "Escanteios 3- Vias Mais/Menos": o total EXATO é um terceiro
    resultado que faz over E under PERDEREM — não é devolução de aposta. Precificar isso
    com massa de push (EV = p*odd + p_push - 1) credita uma devolução que não existe e
    infla o EV; e o par over/under sozinho tem margem irreal (~0,4%, porque o terceiro
    resultado leva probabilidade que o de-vig não vê), o que ainda infla o edge.
    Enquanto não capturarmos a odd do 'exato' e liquidarmos os 3 resultados, esses
    mercados ficam FORA do flag de valor (seguem no board, como oferta visível).
    Achado da auditoria 15/07: 2 sinais sobreviventes do Corinthians×Remo vinham daqui.
    """
    nm = (ln_.get("market_type_name") or "").lower()
    nm = nm.replace(" ", "").replace("-", "").replace("_", "")
    return ("3vias" in nm or "3way" in nm or "tresvias" in nm or "trêsvias" in nm
            or "3caminhos" in nm)
