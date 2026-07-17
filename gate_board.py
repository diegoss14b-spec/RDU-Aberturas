# -*- coding: utf-8 -*-
"""Gate do board: captura, baseline por casa/mercado, fixtures e precificação."""
import json, os, re, sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "data" / "odds" / "_status"
BRT = timezone(timedelta(hours=-3))
LIVE_URL = "https://valor-rdu.netlify.app/data/board.js"
from capture_common import pointer_age_hours, _atomic_write_text


def parse_board(txt):
    m = re.search(r"BOARD\s*=\s*", txt or "")
    if not m: raise ValueError("window.BOARD ausente")
    d, _ = json.JSONDecoder().raw_decode(txt, m.end())
    if not isinstance(d, dict): raise ValueError("BOARD não é objeto")
    return d


def board_coverage(board):
    jogos = (board or {}).get("jogos") or []
    house_games, house_markets = defaultdict(set), defaultdict(lambda: defaultdict(set))
    market_games = defaultdict(int)
    sofa_games = value_games = value_sofa = 0
    for idx, jogo in enumerate(jogos):
        if jogo.get("sofa_id"): sofa_games += 1
        if jogo.get("valor"):
            value_games += 1
            if jogo.get("sofa_id"): value_sofa += 1
        for market, per_house in (jogo.get("mercados") or {}).items():
            if not isinstance(per_house, dict): continue
            active = [h for h, lines in per_house.items() if lines]
            if active: market_games[market] += 1
            for house in active:
                house_games[house].add(idx); house_markets[house][market].add((idx, "total"))
        for market, sides in (jogo.get("times") or {}).items():
            for side_name, side in (sides or {}).items():
                for house, lines in ((side or {}).get("casas") or {}).items():
                    if lines:
                        house_games[house].add(idx)
                        house_markets[house][market].add((idx, str(side_name)))
    return {
        "n_games": len(jogos), "sofa_games": sofa_games,
        "sofa_pct": 100 * sofa_games / len(jogos) if jogos else 0,
        "value_games": value_games, "value_sofa": value_sofa,
        "value_sofa_pct": 100 * value_sofa / value_games if value_games else 100,
        "houses": {h: len(v) for h, v in house_games.items()},
        "markets": dict(market_games),
        "house_markets": {h: {m: len(items) for m, items in v.items()}
                          for h, v in house_markets.items()},
    }


def baseline_reasons(now_cov, prev_cov):
    reasons = []
    house_ratio = float(os.environ.get("GATE_HOUSE_MIN_RATIO", "0.35"))
    market_ratio = float(os.environ.get("GATE_MARKET_MIN_RATIO", "0.35"))
    min_house = int(os.environ.get("GATE_HOUSE_BASE_MIN", "8"))
    min_market = int(os.environ.get("GATE_MARKET_BASE_MIN", "8"))
    for house, before in (prev_cov.get("houses") or {}).items():
        after = (now_cov.get("houses") or {}).get(house, 0)
        if before >= min_house and after < before * house_ratio:
            reasons.append(f"casa {house} caiu >{(1-house_ratio)*100:.0f}%: {before} → {after} jogos")
    for market, before in (prev_cov.get("markets") or {}).items():
        after = (now_cov.get("markets") or {}).get(market, 0)
        if before >= min_market and after < before * market_ratio:
            reasons.append(f"mercado {market} caiu >{(1-market_ratio)*100:.0f}%: {before} → {after} jogos")
    pair_ratio = float(os.environ.get("GATE_HOUSE_MARKET_MIN_RATIO", str(market_ratio)))
    pair_min = int(os.environ.get("GATE_HOUSE_MARKET_BASE_MIN", "5"))
    for house, markets in (prev_cov.get("house_markets") or {}).items():
        current = (now_cov.get("house_markets") or {}).get(house) or {}
        for market, before in (markets or {}).items():
            after = int(current.get(market) or 0)
            if int(before or 0) >= pair_min and after < int(before) * pair_ratio:
                reasons.append(
                    f"casa/mercado {house}/{market} caiu >{(1-pair_ratio)*100:.0f}%: "
                    f"{before} → {after} instrumentos"
                )
    return reasons


def status_reasons(summary):
    reasons = []
    if summary and not summary.get("deploy_allowed", True):
        reasons.append(f"summary: {summary.get('reason')}")
    for casa, st in (summary.get("per_casa") or {}).items():
        if not st.get("ok"): continue
        if int(st.get("n_events") or 0) > 0 and st.get("pointer_valid") is not True:
            reasons.append(f"{casa}: status ok com pointer inválido")
        if int(st.get("n_events") or 0) > 0 and int(st.get("n_markets") or 0) <= 0:
            reasons.append(f"{casa}: status ok sem n_markets")
    return reasons


def sofa_reasons(cov, sofa):
    reasons = []
    if not sofa:
        return ["SofaScore: status ausente"]
    if sofa.get("pointer_valid") is not True:
        reasons.append("SofaScore: pointer/arquivo de fixtures inválido")
    age = sofa.get("pointer_age_h")
    if age is None:
        age = pointer_age_hours({"at": sofa.get("pointer_at")})
    max_age = float(os.environ.get("SOFA_GATE_MAX_AGE_H", "12"))
    if age is None:
        reasons.append("SofaScore: timestamp do pointer inválido")
    elif float(age) > max_age:
        reasons.append(f"SofaScore defasado: {float(age):.1f}h > {max_age:g}h")
    min_board = float(os.environ.get("SOFA_GATE_BOARD_MIN_PCT", "15"))
    if cov["n_games"] >= 10 and cov["sofa_pct"] < min_board:
        reasons.append(f"cobertura Sofa geral baixa: {cov['sofa_pct']:.1f}% < {min_board:g}%")
    min_value = float(os.environ.get("SOFA_GATE_VALUE_MIN_PCT", "70"))
    if cov["value_games"] >= 3 and cov["value_sofa_pct"] < min_value:
        reasons.append(f"cobertura Sofa nos jogos com valor baixa: {cov['value_sofa_pct']:.1f}% < {min_value:g}%")
    return reasons


def load_json(path):
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception: return {}


def clear_blocked_marker(path=None):
    """Remove a stale deploy-block marker after a successful gate."""
    marker = Path(path) if path is not None else STATUS / "blocked_deploy.json"
    marker.unlink(missing_ok=True)


def load_sofa_state(status):
    out = dict(status or {})
    ptr = ROOT / "data" / "fixtures" / "sofa_latest.json"
    try:
        meta = json.loads(ptr.read_text(encoding="utf-8"))
        data = json.loads((ptr.parent / meta["file"]).read_text(encoding="utf-8"))
        n = len(data.get("fixtures") or [])
        valid = n > 0 and n == int(meta.get("n") or 0)
        if valid:
            out.update({"pointer_valid": True, "pointer_file": meta.get("file"),
                        "pointer_at": meta.get("at") or meta.get("ts"),
                        "pointer_n": n, "pointer_age_h": pointer_age_hours(meta)})
    except Exception:
        out.setdefault("pointer_valid", False)
    return out

def main():
    new = parse_board((ROOT / "valor" / "data" / "board.js").read_text(encoding="utf-8"))
    summary = load_json(STATUS / "summary.json")
    sofa = load_sofa_state(load_json(STATUS / "sofa.json") or summary.get("fixtures") or {})
    prev = None
    try:
        from curl_cffi import requests as cr
        r = cr.get(LIVE_URL, timeout=20, impersonate="chrome124")
        if r.status_code == 200: prev = parse_board(r.text)
    except Exception as e:
        print(f"[gate] aviso: não li o board ao vivo ({type(e).__name__}) — sigo sem baseline")

    now_cov = board_coverage(new); prev_cov = board_coverage(prev or {})
    reasons = status_reasons(summary) + sofa_reasons(now_cov, sofa)
    if prev is not None:
        if now_cov["n_games"] < 0.5 * prev_cov["n_games"] and prev_cov["n_games"] >= 10:
            reasons.append(f"jogos caíram >50%: {prev_cov['n_games']} → {now_cov['n_games']}")
        reasons += baseline_reasons(now_cov, prev_cov)

    mod = new.get("model") or {}; mod_status = (mod.get("status") or "").lower()
    if mod_status and mod_status not in ("production", "promoted"):
        reasons.append(f"model.status={mod.get('status')!r} (só production/promoted no deploy)")
    shadow_flags = sum(1 for j in new.get("jogos") or [] for v in j.get("valor") or []
                       if (v.get("model_status") or "").lower().startswith("shadow"))
    if shadow_flags: reasons.append(f"{shadow_flags} flags de valor com model_status shadow")

    n_bad_margin = n_3way = n_past = 0
    for j in new.get("jogos") or []:
        if j.get("game_state") in ("started", "finished"):
            n_past += len(j.get("valor") or [])
        for v in j.get("valor") or []:
            for ln in (((j.get("mercados") or {}).get(v.get("mercado") or "") or {}).get(v.get("casa") or "") or []):
                try:
                    if abs(float(ln.get("linha")) - float(v.get("linha"))) > 1e-9: continue
                except Exception: continue
                o, u = ln.get("over"), ln.get("under")
                if o and u and o > 1 and u > 1 and 1/o + 1/u - 1 < -1e-6: n_bad_margin += 1
                nm = (ln.get("market_type_name") or "").lower().replace(" ", "").replace("-", "")
                if any(x in nm for x in ("3vias", "3way", "tresvias", "trêsvias")): n_3way += 1
    if n_bad_margin: reasons.append(f"{n_bad_margin} flags de valor com margem negativa no par")
    if n_3way: reasons.append(f"{n_3way} flags de valor em mercado de 3 vias")
    if n_past: reasons.append(f"{n_past} flags de valor em jogo started/finished")

    print(f"[gate] agora: {now_cov['n_games']} jogos · casas={now_cov['houses']} · mercados={now_cov['markets']} · Sofa={now_cov['sofa_pct']:.1f}%/valor {now_cov['value_sofa_pct']:.1f}%")
    if reasons:
        blocked = {"ts_brt": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
                   "reasons": reasons, "now": now_cov, "prev": prev_cov,
                   "sofa": sofa, "model": mod}
        STATUS.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(STATUS / "blocked_deploy.json", json.dumps(blocked, ensure_ascii=False, indent=1))
        print("[gate] ❌ DEPLOY BLOQUEADO — site antigo permanece no ar:")
        for reason in reasons: print(f"   - {reason}")
        sys.exit(3)
    clear_blocked_marker()
    print("[gate] ✅ liberado pra deploy")
    sys.exit(0)


if __name__ == "__main__": main()
