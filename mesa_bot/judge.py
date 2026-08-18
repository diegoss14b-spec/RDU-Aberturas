# -*- coding: utf-8 -*-
"""Juiz independente: odds do board.js × candidate_pricer (mesma régua da Mesa).

Não lê BOARD.valor[]. Percorre mercados[canon][casa] → price → EV/edge gate.
Identidade: home_id/away_id/comp no board (após build_board novo) OU fixtures
locais em valor-app/data/fixtures (Actions checkout).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from signals import signal_key

# Mesmos limiares de build_board.py (não divergir sem decisão)
EV_MIN, EDGE_MIN = 0.05, 0.04
MARGIN_MIN, MARGIN_CAP = 0.0, 0.12
P_LO, P_HI = 0.15, 0.85

MODELO = {
    "Cartões": "cartoes",
    "Faltas": "faltas",
    "Finalizações": "finalizacoes",
    "Escanteios": "escanteios",
}

# Fallback se import de build_board falhar (espelho — não inventar rótulos).
_FIXTURE_LABEL_COMP_FALLBACK = {
    "BR-A": "BR-A", "BR-B": "BR-B", "EPL": "PL", "LaLiga": "LL", "SerieA": "SA",
    "Bundesliga": "BU", "Ligue1": "L1", "MLS": "MLS", "Argentina": "ARG",
    "ARG2": "ARG2", "CSL": "CHN", "Allsvenskan": "SWE", "Uruguay": "URU",
    "Ecuador": "ECU", "Russia": "RUS", "Eliteserien": "NOR", "LigaMX": "MEX",
    "MEXE": "MEXE", "BOL": "BOL", "CHI": "CHI", "PER": "PER", "COL": "COL",
    "COL2": "COL2", "CZE": "CZE", "ROU": "ROU", "DEN": "DEN", "SUI": "SUI",
    "EFLC": "EFLC", "NED": "NED", "GER2": "GER2",
    "SCO": "SCO", "ENG2": "ENG2", "BEL": "BEL", "POR": "POR",
    "ITA2": "ITA2", "JPN": "JPN", "AUT": "AUT", "POL": "POL",
    "TUR": "TUR", "GRE": "GRE", "SAU": "SAU", "UKR": "UKR",
    "CRO": "CRO", "SRB": "SRB", "BUL": "BUL", "VEN": "VEN",
    "PAR": "PAR", "AUS": "AUS", "ESP2": "ESP2", "FRA2": "FRA2",
    "CDF": "CDF", "CDI": "CDI", "CDR": "CDR", "DFB": "DFB", "FAC": "FAC",
}


def _fixture_label_comp(root: Path) -> dict:
    # Não importar build_board (puxa value_pricers + side-effects). Dict espelhado.
    return _FIXTURE_LABEL_COMP_FALLBACK


def _valor_root() -> Optional[Path]:
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here.parent / "valor-app"):
        if (cand / "candidate_pricer.py").exists():
            return cand
    return None


def _ensure_pricers():
    root = _valor_root()
    if root is None:
        raise RuntimeError(
            "candidate_pricer.py não encontrado (rode a partir de valor-app "
            "ou do monorepo Claude com pasta valor-app/)"
        )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from candidate_pricer import (  # noqa: WPS433
        CardsPricer, CornersPricer, FoulsPricer, ShotsPricer,
    )
    from pricing_math import (  # noqa: WPS433
        edge_vs_market, expected_value, fair_odd, is_integer_line,
    )
    cp, sp, fp, xp = CardsPricer(), ShotsPricer(), FoulsPricer(), CornersPricer()
    if not all(getattr(p, "ok", False) for p in (cp, sp, fp, xp)):
        raise RuntimeError("bundle candidate_pricer incompleto (.ok=False)")
    return {
        "pricers": {
            "cartoes": cp,
            "finalizacoes": sp,
            "faltas": fp,
            "escanteios": xp,
        },
        "expected_value": expected_value,
        "edge_vs_market": edge_vs_market,
        "fair_odd": fair_odd,
        "is_integer_line": is_integer_line,
        "model_status": "promoted",
        "model_source": "candidate_pricer",
        "root": root,
    }


_CTX = None
_FX_BY_SOFA: Optional[Dict[int, dict]] = None


def _ctx():
    global _CTX
    if _CTX is None:
        _CTX = _ensure_pricers()
    return _CTX


def _load_fixtures(root: Path) -> Dict[int, dict]:
    global _FX_BY_SOFA
    if _FX_BY_SOFA is not None:
        return _FX_BY_SOFA
    out: Dict[int, dict] = {}
    latest = root / "data" / "fixtures" / "sofa_latest.json"
    snap = None
    if latest.exists():
        try:
            meta = json.loads(latest.read_text(encoding="utf-8"))
            snap = root / "data" / "fixtures" / meta["file"]
        except Exception:
            snap = None
    if snap and snap.exists():
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
            for fx in data.get("fixtures") or []:
                sid = fx.get("sofa_id")
                if sid is None:
                    continue
                out[int(sid)] = fx
        except Exception:
            pass
    _FX_BY_SOFA = out
    return out


def resolve_identity(j: dict, root: Path) -> Optional[Tuple[str, int, int]]:
    """Retorna (comp, home_id, away_id) ou None."""
    hid, aid = j.get("home_id"), j.get("away_id")
    comp = j.get("comp")
    if hid is not None and aid is not None and comp:
        try:
            return str(comp), int(hid), int(aid)
        except (TypeError, ValueError):
            pass

    sofa = j.get("sofa_id")
    if sofa is None:
        return None
    try:
        sid = int(sofa)
    except (TypeError, ValueError):
        return None
    fx = _load_fixtures(root).get(sid)
    if not fx:
        return None
    label = fx.get("label") or j.get("fx_label")
    mmap = _fixture_label_comp(root)
    comp2 = mmap.get(label) if label else None
    if not comp2:
        return None
    try:
        return comp2, int(fx["home_id"]), int(fx["away_id"])
    except (KeyError, TypeError, ValueError):
        return None


def de_vig(over, under) -> Optional[dict]:
    if not over or not under or over <= 1 or under <= 1:
        return None
    po, pu = 1 / over, 1 / under
    tot = po + pu
    return {"p_over": po / tot, "p_under": pu / tot, "margin": tot - 1}


def three_way(ln_: dict) -> bool:
    nm = (ln_.get("market_type_name") or "").lower()
    nm = nm.replace(" ", "").replace("-", "").replace("_", "")
    return (
        "3vias" in nm or "3way" in nm or "tresvias" in nm or "trêsvias" in nm
        or "3caminhos" in nm
    )


def judge_board(board: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lista de sinais com EV≥gate, ordenada por EV desc."""
    ctx = _ctx()
    pricers = ctx["pricers"]
    expected_value = ctx["expected_value"]
    edge_vs_market = ctx["edge_vs_market"]
    fair_odd = ctx["fair_odd"]
    is_integer_line = ctx["is_integer_line"]
    root = ctx["root"]

    out: List[Dict[str, Any]] = []
    n_skip_id = n_skip_price = n_skip_gate = 0

    for j in board.get("jogos") or []:
        if not isinstance(j, dict):
            continue
        if j.get("game_state") and j.get("game_state") != "upcoming":
            continue
        sofa_id = j.get("sofa_id")
        if sofa_id is None:
            continue
        ident = resolve_identity(j, root)
        if not ident:
            n_skip_id += 1
            continue
        comp, hid, aid = ident
        stale_set = set(j.get("stale_casas") or [])
        base = {
            "jogo": j.get("jogo") or "%s × %s" % (j.get("home") or "?", j.get("away") or "?"),
            "home": j.get("home"),
            "away": j.get("away"),
            "liga": j.get("liga") or "",
            "sofa_id": sofa_id,
            "inicio": j.get("inicio"),
            "inicio_iso": j.get("inicio_iso"),
            "game_state": j.get("game_state"),
            "comp": comp,
            "home_id": hid,
            "away_id": aid,
            "judge": "candidate_pricer",
        }
        mercados = j.get("mercados") or {}
        for canon, model in MODELO.items():
            rows_by_casa = mercados.get(canon)
            if not rows_by_casa:
                continue
            pricer = pricers.get(model)
            if not pricer:
                continue
            for casa, linhas in rows_by_casa.items():
                if casa in stale_set:
                    continue
                if not isinstance(linhas, list):
                    continue
                for ln_ in linhas:
                    if not isinstance(ln_, dict):
                        continue
                    if three_way(ln_):
                        continue
                    try:
                        linha = float(ln_["linha"])
                        o = float(ln_["over"])
                        u = float(ln_["under"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    pr = pricer.price(comp, hid, aid, linha)
                    if not pr:
                        n_skip_price += 1
                        continue
                    dv = de_vig(o, u)
                    if not dv or not (
                        MARGIN_MIN - 1e-9 <= dv["margin"] <= MARGIN_CAP + 1e-9
                    ):
                        n_skip_gate += 1
                        continue
                    pp = float(pr.get("p_push") or 0.0)
                    for side, oddk, odd in (
                        ("over", "over", o),
                        ("under", "under", u),
                    ):
                        our_p = float(
                            pr.get("p_" + side + "_win", pr.get("p_" + side, 0.0))
                        )
                        if our_p < P_LO or our_p > P_HI:
                            continue
                        edge = edge_vs_market(our_p, dv["p_" + side], pp)
                        ev = expected_value(our_p, odd, pp)
                        if ev < EV_MIN or edge < EDGE_MIN:
                            continue
                        fo = fair_odd(our_p, pp)
                        sig = dict(base)
                        sig.update({
                            "mercado": canon,
                            "linha": linha,
                            "lado": "Mais" if side == "over" else "Menos",
                            "casa": casa,
                            "odd": odd,
                            "nossa_prob": round(our_p * 100, 1),
                            "p_push": round(pp * 100, 1),
                            "push_line": bool(is_integer_line(linha)),
                            "edge_pp": round(edge * 100, 1),
                            "ev_pct": round(ev * 100, 1),
                            "fair_odd": round(fo, 2) if fo else None,
                            "mu": round(pr.get("mu_cal", pr["mu"]), 1),
                            "mu_cal": round(pr.get("mu_cal", pr["mu"]), 1),
                            "mu_raw": round(pr.get("mu_raw", pr["mu"]), 1),
                            "model_status": ctx["model_status"],
                            "model_source": ctx["model_source"],
                            "actionable": True,
                        })
                        sig["key"] = signal_key(sig)
                        out.append(sig)

    out.sort(key=lambda s: float(s.get("ev_pct") or 0), reverse=True)
    print(
        "judge: sinais=%d skip_id=%d skip_price=%d skip_gate~=%d"
        % (len(out), n_skip_id, n_skip_price, n_skip_gate),
        flush=True,
    )
    return out
