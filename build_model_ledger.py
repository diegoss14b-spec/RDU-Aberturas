# -*- coding: utf-8 -*-
"""build_model_ledger.py — REGISTRO FORWARD modelo × odds (31/07/2026).

Materializa, a cada rodada, o que o build_board já calcula por dentro: o P(over)
do MODELO PUBLICADO (bundle candidate_pricer_data.json — o MESMO que dá o selo de
valor do board) para TODA linha de Faltas/Finalizações/Cartões/Escanteios com
identidade Sofa, e appenda num ledger imutável quando a key liquida. Cada dia de
Mesa vira amostra de backtest SEM reconstruir OOF retroativo (fonte:
research/backtest_odds_2026-07-30 — em ~100 dias de registro decide-se um edge
de 10%).

Duas passadas, ambas idempotentes:

1. PRICE (carimbo) — key dos 4 mercados com sofa_id e fixture conhecida ganha
   campos aditivos no PRÓPRIO record (m_p_over, m_mu, m_ver, …). O carimbo é
   ÚNICO: retreino posterior NÃO re-carimba (o ledger registra o modelo que
   estava no ar). Par ausente no bundle carimba m_p_over=null — é o buraco
   medível (B4); enquanto a key está pré-kickoff, um bundle novo pode preencher.
   TRAVA ANTI-VAZAMENTO: carimba se now < kickoff (bundle publicado é informação
   pré-jogo); pós-kickoff só se a versão do bundle (data do retreino) ≤ dia do
   jogo — o modelo não pode ter visto o jogo.

2. EMIT — key settled + carimbada e sem m_emitted vira UMA linha em
   data/odds_history/ledger/{YYYY-MM}.jsonl (mês do kickoff) e o record marca
   m_emitted. Dedupe extra na escrita: as keys já presentes no arquivo do mês
   são recarregadas antes do append (protege contra crash entre append e mark).
   O ledger NUNCA é reescrito nem podado (lição do CLV do chasing).

Fixture index (data/odds_history/ledger/fixture_index.json): as fixtures da Sofa
são uma janela rolante (−6h → +12d); o index acumula sofa_id → (home_id,
away_id, label) para o carimbo tardio (caso B1: sofa_id_settle chega no settle,
com o jogo já fora da janela) continuar resolvendo ids.

SEMÂNTICA DE PUSH (B3): linha INTEIRA liquida diferente entre casas — medido no
backtest de 30/07: bet365 precifica "push conta contra" (over-round 0,992) e a
Pinnacle preço condicionado no-push (1,068). Cada linha do ledger sai com
`linha_inteira` + `push_sem` (mapa PUSH_SEMANTICS, override opcional em
data/config/push_semantics.json). REGRA DO CONSUMIDOR: ROI/backtest usa só
linhas .5 por default; linha inteira entra apenas com push_sem conhecido.
`won` da Mesa segue a lei do push = devolve stake (won=null) — inalterado.

Fail-closed sem derrubar o pipeline: bundle ausente/ilegível = no-op com aviso
(o step de histórico do valor.yml não pode morrer por causa do ledger); erro
inesperado loga ::error:: e sai 0 — o ledger atrasa, a Mesa não para.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from canonical import load_sofa_fixtures, parse_history_key  # noqa: E402
from history_merge import atomic_write_text  # noqa: E402
from history_quality import parse_iso_flex  # noqa: E402

HIST = ROOT / "data" / "odds_history"
LEDGER_DIR = HIST / "ledger"
FIX_INDEX = LEDGER_DIR / "fixture_index.json"
STATUS_FILE = LEDGER_DIR / "_status.json"
BUNDLE_PATH = ROOT / "data" / "candidate_pricer_data.json"
PUSH_SEM_CFG = ROOT / "data" / "config" / "push_semantics.json"
BRT = timezone(timedelta(hours=-3))

MKT_PT2EN = {"Cartões": "cards", "Faltas": "fouls",
             "Finalizações": "shots", "Escanteios": "corners"}

# label da fixture (fetch_fixtures_sofascore.TOURNAMENTS) → comp dos lookups do
# modelo. Label fora do mapa → não carimba, não emite (ex.: WC encerrada).
# Cups (CL/EL/LIB/SUD/CDB) entram MAPEADOS de propósito: o bundle de hoje não as
# carrega, então saem como m_p_over=null — o buraco vira contável, não invisível.
LABEL2COMP = {
    "BR-A": "BR-A", "BR-B": "BR-B", "BR-CdB": "CDB",
    "EPL": "PL", "LaLiga": "LL", "SerieA": "SA", "Bundesliga": "BU",
    "Ligue1": "L1", "MLS": "MLS", "Argentina": "ARG", "CSL": "CHN",
    "Allsvenskan": "SWE", "Uruguay": "URU", "Ecuador": "ECU", "Russia": "RUS",
    "Eliteserien": "NOR", "LigaMX": "MEX",
    "UCL": "CL", "UEL": "EL", "Libertadores": "LIB", "Sudamericana": "SUD",
    # B5 (31/07): ligas do modelo adicionadas às fixtures nesta mesma entrega
    "BOL": "BOL", "CHI": "CHI", "PER": "PER", "COL": "COL", "COL2": "COL2",
    "ARG2": "ARG2", "CZE": "CZE", "ROU": "ROU", "DEN": "DEN", "SUI": "SUI",
    "CDA": "CDA", "MEXE": "MEXE",
    # ── 05/08: as 29 ligas que entraram nas fixtures. O teste
    # `test_label2comp_cobre_todas_as_fixtures` reprovou o commit anterior por
    # esta falta exata — é o guard contra o meio-feito, e ele funcionou:
    # torneio buscado sem comp aqui cai em `label_fora_do_mapa` e some do ledger,
    # ou seja, o jogo entraria no board e ficaria fora do estudo de CLV.
    "EFLC": "EFLC", "ECL": "ECL", "NED": "NED", "GER2": "GER2", "SCO": "SCO",
    "ENG2": "ENG2", "BEL": "BEL", "POR": "POR", "ITA2": "ITA2", "JPN": "JPN",
    "AUT": "AUT", "POL": "POL", "TUR": "TUR", "GRE": "GRE", "SAU": "SAU",
    "UKR": "UKR", "CRO": "CRO", "SRB": "SRB", "BUL": "BUL", "VEN": "VEN",
    "PAR": "PAR", "AUS": "AUS", "ESP2": "ESP2", "FRA2": "FRA2",
    "CDF": "CDF", "CDI": "CDI", "CDR": "CDR", "DFB": "DFB", "FAC": "FAC",
}

# B3 — semântica de liquidação da CASA em linha inteira (medida, não presumida).
PUSH_SEMANTICS_DEFAULT = {
    "bet365": "counts_against",        # over-round 0,992 nas inteiras (backtest 30/07)
    "pinnacle": "conditional_no_push",  # over-round 1,068 — preço condicionado no-push
}


def log(msg):
    print(f"[ledger] {msg}", flush=True)


def load_push_semantics():
    out = dict(PUSH_SEMANTICS_DEFAULT)
    try:
        if PUSH_SEM_CFG.is_file():
            extra = json.loads(PUSH_SEM_CFG.read_text(encoding="utf-8"))
            if isinstance(extra, dict):
                out.update({str(k).lower(): str(v) for k, v in extra.items()})
    except Exception as exc:
        log(f"⚠ push_semantics.json ilegível ({exc}) — seguindo com o default")
    return out


def _dt(value):
    return parse_iso_flex(value, default_tz=BRT)


def _pre_ko(ts, kickoff):
    """Estrito como na fase 1 do backtest: os DOIS stamps precisam parsear."""
    t, k = _dt(ts), _dt(kickoff)
    return bool(t and k and t < k)


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def is_integer_line(line):
    try:
        L = float(line)
    except (TypeError, ValueError):
        return False
    return abs(L - round(L)) < 1e-9


def update_fixture_index(fixtures=None):
    """Acumula sofa_id → ids/label a partir da janela rolante. Nunca poda."""
    idx = {}
    try:
        if FIX_INDEX.is_file():
            idx = json.loads(FIX_INDEX.read_text(encoding="utf-8"))
            if not isinstance(idx, dict):
                idx = {}
    except Exception:
        idx = {}
    fresh = 0
    for f in (fixtures if fixtures is not None else load_sofa_fixtures()):
        sid, hid, aid = f.get("sofa_id"), f.get("home_id"), f.get("away_id")
        if not sid or hid is None or aid is None:
            continue
        entry = {"h": int(hid), "a": int(aid), "lb": f.get("label") or "",
                 "ko": f.get("start_utc")}
        if idx.get(str(sid)) != entry:
            idx[str(sid)] = entry
            fresh += 1
    if fresh:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(FIX_INDEX, json.dumps(idx, ensure_ascii=False))
    return idx, fresh


def load_bundle():
    """(pricers, version, sha8, ref_policy) ou (None, …) — fail-closed."""
    if not BUNDLE_PATH.is_file():
        return None, None, None, {}
    try:
        meta = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
        version = str(meta.get("version") or "")
        # a trava anti-vazamento depende da versão ser uma DATA legível
        datetime.strptime(version, "%Y-%m-%d")
        sha8 = str(meta.get("data_sha256") or "")[:8] or None
        ref_policy = {m: (v.get("ref_policy") or "neutral")
                      for m, v in (meta.get("markets") or {}).items()}
    except Exception as exc:
        log(f"⚠ bundle ilegível/sem versão-data ({exc}) — carimbo pulado nesta rodada")
        return None, None, None, {}
    from candidate_pricer import CardsPricer, CornersPricer, FoulsPricer, ShotsPricer
    pricers = {"cards": CardsPricer(), "fouls": FoulsPricer(),
               "shots": ShotsPricer(), "corners": CornersPricer()}
    if not any(p.ok for p in pricers.values()):
        log("⚠ nenhum pricer ok no bundle — carimbo pulado nesta rodada")
        return None, None, None, {}
    return pricers, version, sha8, ref_policy


def _key_identity(key, record):
    """(meta, market_en, sofa_id_forte, sofa_id_settle) — None se fora do escopo."""
    meta = parse_history_key(key)
    market = MKT_PT2EN.get(meta.get("mercado") or "")
    if not market:
        return None
    if (meta.get("lado") or "over") not in ("over", "under"):
        return None                      # handicap casa/fora fica fora do ledger
    sid = _int(record.get("sofa_id") or meta.get("sofa_id"))
    sid_settle = _int(record.get("sofa_id_settle"))
    return meta, market, sid, sid_settle


def stamp_records(merged, fixidx, pricers, version, sha8, now):
    """Passada 1 — carimbo do modelo publicado. Muta os records; devolve contagens."""
    n = Counter()
    if not pricers:
        return n
    for key, rec in merged.items():
        if key.startswith("__") or not isinstance(rec, dict):
            continue
        ident = _key_identity(key, rec)
        if ident is None:
            continue
        meta, market, sid, sid_settle = ident
        sid_eff = sid or sid_settle
        if not sid_eff:
            n["sem_sofa_id"] += 1
            continue
        if rec.get("m_emitted"):
            continue                     # já foi pro ledger — imutável
        if rec.get("m_ts") and rec.get("m_p_over") is not None:
            continue                     # carimbo é único
        fx = fixidx.get(str(sid_eff))
        if not fx:
            n["sem_fixture_no_index"] += 1
            continue
        comp = LABEL2COMP.get(fx.get("lb") or "")
        if not comp:
            n["label_fora_do_mapa"] += 1
            continue
        line = _num(meta.get("linha"))
        if line is None:
            n["linha_ilegivel"] += 1
            continue
        kickoff = rec.get("kickoff") or fx.get("ko")
        ko_dt = _dt(kickoff)
        pre_ko = bool(ko_dt and now < ko_dt)
        if not pre_ko:
            # trava anti-vazamento: pós-kickoff, o bundle tem que ser de retreino
            # ESTRITAMENTE anterior ao dia do jogo. `<=` era furado (ataque
            # 31/07): o retreino da versão X inclui jogos jogados NO dia X
            # (ex.: VERSION 2026-07-26, base "até 26/07") — com version ==
            # game_day o jogo podia estar na própria base de treino. Pré-KO não
            # tem esse problema (o jogo ainda não aconteceu quando carimbou).
            game_day = (str(kickoff)[:10] if kickoff else "") or (meta.get("day") or "")
            if not game_day or not (version < game_day):
                n["pos_ko_bloqueado_pela_versao"] += 1
                continue
        pr = pricers.get(market)
        res = pr.price(comp, fx.get("h"), fx.get("a"), line) if pr else None
        if res is None and rec.get("m_ts") and rec.get("m_ver") == version:
            continue        # buraco já registrado NESTE bundle — não re-grava à toa
        rec["m_ver"] = version
        rec["m_sha8"] = sha8
        rec["m_comp"] = comp
        rec["m_ts"] = now.isoformat(timespec="seconds")
        rec["m_pre_ko"] = pre_ko
        if res:
            rec["m_p_over"] = round(float(res["p_over_win"]), 4)
            rec["m_p_push"] = round(float(res["p_push"]), 4)
            rec["m_mu"] = round(float(res["mu_cal"]), 3)
            rec["m_mu_raw"] = round(float(res["mu_raw"]), 4)
            n["carimbadas"] += 1
        else:
            rec["m_p_over"] = None       # buraco medível (B4): comp sem par no bundle
            rec["m_p_push"] = None
            rec["m_mu"] = None
            rec["m_mu_raw"] = None
            n["carimbadas_sem_par_no_bundle"] += 1
    return n


def _ledger_line(key, rec, meta, market, sid, sid_settle, fx, push_map, ref_policy):
    line = _num(meta.get("linha"))
    casa = meta.get("casa") or key.split("|")[0]
    kickoff = rec.get("kickoff") or (fx or {}).get("ko")
    int_line = is_integer_line(line)
    return {
        "key": key,
        "date": (str(kickoff)[:10] if kickoff else "") or meta.get("day"),
        "kickoff": kickoff,
        "sofa_id": sid,
        "sofa_id_settle": sid_settle,
        "id_origem": "sofa" if sid else "settle_feed",
        "casa": casa,
        "mercado": meta.get("mercado"),
        "market": market,
        "linha": line,
        "lado": meta.get("lado"),
        "linha_inteira": int_line,
        "push_sem": (push_map.get(casa.lower(), "unknown") if int_line else None),
        "odd_open": rec.get("open_odd"),
        "open_ts": rec.get("open_ts"),
        "odd_close": rec.get("close_odd"),
        "close_ts": rec.get("close_ts"),
        "min_odd": rec.get("min_odd"),
        "max_odd": rec.get("max_odd"),
        "n_moves": rec.get("n_moves"),
        "capture_quality": rec.get("capture_quality"),
        # recomputados contra o kickoff (não confia no capture_quality — fase 1)
        "open_pre_ko": _pre_ko(rec.get("open_ts"), kickoff),
        "close_pre_ko": bool(rec.get("close_odd")) and _pre_ko(rec.get("close_ts"), kickoff),
        "comp": rec.get("m_comp"),
        "home_id": (fx or {}).get("h"),
        "away_id": (fx or {}).get("a"),
        "p_over": rec.get("m_p_over"),
        "p_push": rec.get("m_p_push"),
        "mu": rec.get("m_mu"),
        "mu_raw": rec.get("m_mu_raw"),
        "model_version": rec.get("m_ver"),
        "bundle_sha8": rec.get("m_sha8"),
        "ref_policy": ref_policy.get(market, "neutral"),
        "priced_pre_ko": rec.get("m_pre_ko"),
        "result": rec.get("result"),
        "won": rec.get("won"),
        "result_yellows": rec.get("result_yellows"),
        "result_reds": rec.get("result_reds"),
        "date_offset": rec.get("settlement_date_offset") or 0,
        "settlement_source": rec.get("settlement_source"),
    }


def emit_ledger(merged, fixidx, push_map, ref_policy, now, ledger_dir=None):
    """Passada 2 — appenda keys liquidadas+carimbadas no jsonl mensal. Contagens."""
    ledger_dir = Path(ledger_dir or LEDGER_DIR)
    n = Counter()
    by_month = {}
    for key, rec in merged.items():
        if key.startswith("__") or not isinstance(rec, dict):
            continue
        if rec.get("status") != "settled" or not rec.get("m_ts") or rec.get("m_emitted"):
            continue
        ident = _key_identity(key, rec)
        if ident is None:
            continue
        meta, market, sid, sid_settle = ident
        sid_eff = sid or sid_settle
        fx = fixidx.get(str(sid_eff)) if sid_eff else None
        kickoff = str(rec.get("kickoff") or (fx or {}).get("ko") or "")
        month = kickoff[:7] if len(kickoff) >= 7 else (str(meta.get("day") or "")[:7]
                                                       or now.strftime("%Y-%m"))
        by_month.setdefault(month, []).append(
            (key, rec, _ledger_line(key, rec, meta, market, sid, sid_settle,
                                    fx, push_map, ref_policy)))
    if not by_month:
        return n
    ledger_dir.mkdir(parents=True, exist_ok=True)
    for month, items in sorted(by_month.items()):
        path = ledger_dir / f"{month}.jsonl"
        seen = set()
        if path.is_file():                     # dedupe pós-crash: append sem mark
            with path.open(encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        seen.add(json.loads(raw).get("key"))
                    except ValueError:
                        continue
        with path.open("a", encoding="utf-8") as handle:
            for key, rec, line in items:
                if key in seen:
                    rec["m_emitted"] = now.isoformat(timespec="seconds")
                    n["ja_no_arquivo_so_marcadas"] += 1
                    continue
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
                rec["m_emitted"] = now.isoformat(timespec="seconds")
                n["emitidas"] += 1
    return n


def main():
    now = datetime.now(BRT)
    from history_settle import consolidate_key_documents, persist_consolidated_documents
    import glob as _glob
    paths = [Path(p) for p in sorted(_glob.glob(str(HIST / "keys" / "*.json")))]
    if not paths:
        log("sem keys/*.json — nada a fazer")
        return 0
    documents = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    merged, owners, _dups = consolidate_key_documents(documents)

    fixidx, fresh = update_fixture_index()
    pricers, version, sha8, ref_policy = load_bundle()
    push_map = load_push_semantics()

    n_stamp = stamp_records(merged, fixidx, pricers, version, sha8, now)
    n_emit = emit_ledger(merged, fixidx, push_map, ref_policy, now)

    changed = 0
    if n_stamp.get("carimbadas") or n_stamp.get("carimbadas_sem_par_no_bundle") \
            or n_emit.get("emitidas") or n_emit.get("ja_no_arquivo_so_marcadas"):
        changed = persist_consolidated_documents(documents, merged, owners)

    status = {
        "generated_at": now.isoformat(timespec="seconds"),
        "bundle_version": version, "bundle_sha8": sha8,
        "fixture_index_total": len(fixidx), "fixture_index_novas": fresh,
        "carimbo": dict(n_stamp), "emissao": dict(n_emit),
        "arquivos_keys_atualizados": changed,
    }
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STATUS_FILE, json.dumps(status, ensure_ascii=False, indent=1))
    log(f"carimbo: {n_stamp.get('carimbadas', 0):,} com preço · "
        f"{n_stamp.get('carimbadas_sem_par_no_bundle', 0):,} sem par (buraco B4) · "
        f"{n_stamp.get('sem_fixture_no_index', 0):,} sem fixture · "
        f"{n_stamp.get('label_fora_do_mapa', 0):,} label fora do mapa")
    log(f"emissão: {n_emit.get('emitidas', 0):,} linhas novas no ledger · "
        f"{n_emit.get('ja_no_arquivo_so_marcadas', 0):,} já existiam (só marcadas)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # nunca derruba o step de histórico da Mesa
        print(f"::error::[ledger] falha inesperada ({type(exc).__name__}: {exc}) — "
              f"o ledger atrasa nesta rodada, a Mesa segue", flush=True)
        raise SystemExit(0)
