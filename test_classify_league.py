# -*- coding: utf-8 -*-
"""test_classify_league.py — barreira de rótulo do universo dos modelos (31/07/2026).

Caso-gatilho: "Noruega - 1.Division" (Superbet) caía em "norueg" → NOR e o modelo da
Eliteserien precificou Bryne×Strømsgodset (os dois REBAIXADOS em 2025, presentes no
bundle) — flags de −EV real que só não foram ao ar porque o gate de cobertura Sofa
segurou o deploy por acaso. A proteção certa é pelo RÓTULO: vizinho de divisão, copa,
feminino e base têm os MESMOS clubes do bundle, então o casamento por nome não barra.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_board import classify_league  # noqa: E402

# (rótulo, esperado) — esperado None = fora do universo; senão o código de ESCANTEIOS
# (mercado presente em todas as regras exóticas) ou de cartões pra ligas full.
CASOS = [
    # o caso real de 31/07 e vizinhos noruegueses
    ("Noruega - 1.Division", None),
    ("Norway - 1st Division", None),
    ("Noruega - OBOS-ligaen", None),
    ("Noruega - Eliteserien", "NOR"),
    ("Eliteserien", "NOR"),
    ("Noruega - Copa da Noruega", None),          # NM Cupen: times da Eliteserien!
    # Espanha: a 2ª divisão é BRANDED "LaLiga" — mesmo bug em potência
    ("Espanha - LaLiga Hypermotion", None),
    ("LaLiga Hypermotion", None),
    ("Espanha - LaLiga", "LL"),
    ("Espanha - Primera Federación", None),       # 3ª tier: "primera" + "espan"
    ("Espanha - Copa del Rey", None),
    # Brasil: A e B são ligas de modelo; C/D, feminino, sub-20 e copa NÃO
    ("Brasileirão Betano", "BR-A"),
    ("Brasileirão Série B", "BR-B"),
    ("Brazil Serie B", "BR-B"),
    ("Brazil Serie C", None),
    ("Brasileirão Feminino A1", None),
    ("Brasileirão Sub-20", None),
    ("Copa Betano do Brasil", None),
    # China: allowlist (antes "chin" comia "China League One")
    ("Chinese Super League", "CSL"),
    ("Super Liga Chinesa", "CSL"),
    ("China League One", None),
    # Equador: "LigaPro Serie B" é o nome OFICIAL da 2ª divisão
    ("Equador - LigaPro", "ECU"),
    ("Equador - LigaPro Serie B", None),
    # Itália/Alemanha/França/Inglaterra
    ("Itália - Serie A", "SA"),
    ("Itália - Serie C", None),
    ("Alemanha - Bundesliga", "BU"),
    ("Alemanha - 2. Bundesliga", None),
    ("Alemanha - Frauen-Bundesliga", None),
    ("França - Ligue 1", "L1"),
    ("Inglaterra - Premier League", "PL"),
    ("Inglaterra - FA Cup", None),
    ("Bolívia - División Profesional", "BOL"),
]

CODE_OF = {  # como ler o resultado por rótulo esperado
    "BR-A": ("cartoes", "A"), "BR-B": ("cartoes", "B"), "PL": ("cartoes", "PL"),
    "LL": ("cartoes", "LL"), "SA": ("cartoes", "SA"), "BU": ("cartoes", "BU"),
    "L1": ("cartoes", "L1"), "CSL": ("escanteios", "CSL"), "BOL": ("escanteios", "BOL"),
    "ECU": ("escanteios", "ECU"), "NOR": ("escanteios", "NOR"),
}


def main():
    bad = 0
    for label, want in CASOS:
        got = classify_league(label)
        if want is None:
            ok = got is None
            desc = "None"
        else:
            campo, cod = CODE_OF[want]
            ok = got is not None and got.get(campo) == cod
            desc = want
        if not ok:
            bad += 1
            print(f"  FAIL {label!r}: esperado {desc}, veio {got}")
    print(f"classify_league: {len(CASOS) - bad}/{len(CASOS)} casos ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
