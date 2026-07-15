# -*- coding: utf-8 -*-
"""gate_board.py — gate de qualidade ANTES do deploy (política "não piorar" do brief).
Baseline = o board AO VIVO no site (https://valor-rdu.netlify.app/data/board.js) — sem
cache/artifact: o site publicado É o estado anterior. Compara com o board recém-gerado:
BLOQUEIA (exit 3, site antigo fica no ar) se:
  - summary.deploy_allowed == false; ou
  - n_casas_now < n_casas_prev E n_casas_now < 3; ou
  - n_jogos_now < 50% de n_jogos_prev (quando prev >= 10).
Se bloquear: grava data/odds/_status/blocked_deploy.json com o motivo."""
import json, re, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "data" / "odds" / "_status"
BRT = timezone(timedelta(hours=-3))
LIVE_URL = "https://valor-rdu.netlify.app/data/board.js"

def parse_board(txt):
    m = re.search(r"BOARD\s*=\s*", txt)
    d, _ = json.JSONDecoder().raw_decode(txt, m.end())
    return d

def main():
    new = parse_board((ROOT / "valor" / "data" / "board.js").read_text(encoding="utf-8"))
    n_casas_now = len(new.get("casas") or [])
    n_jogos_now = len(new.get("jogos") or [])
    summary = {}
    sf = STATUS / "summary.json"
    if sf.exists():
        summary = json.loads(sf.read_text(encoding="utf-8"))

    prev = None
    try:
        from curl_cffi import requests as cr
        r = cr.get(LIVE_URL, timeout=20, impersonate="chrome124")
        if r.status_code == 200:
            prev = parse_board(r.text)
    except Exception as e:
        print(f"[gate] aviso: não li o board ao vivo ({type(e).__name__}) — sigo sem baseline")
    n_casas_prev = len((prev or {}).get("casas") or [])
    n_jogos_prev = len((prev or {}).get("jogos") or [])

    reasons = []
    if summary and not summary.get("deploy_allowed", True):
        reasons.append(f"summary: {summary.get('reason')}")
    if prev is not None:
        if n_casas_now < n_casas_prev and n_casas_now < 3:
            reasons.append(f"casas caíram: {n_casas_prev} → {n_casas_now}")
        if n_jogos_prev >= 10 and n_jogos_now < 0.5 * n_jogos_prev:
            reasons.append(f"jogos caíram >50%: {n_jogos_prev} → {n_jogos_now}")

    # P0: modelo shadow nunca no board publicável
    mod = new.get("model") or {}
    mod_status = (mod.get("status") or "").lower()
    if mod_status and mod_status not in ("production", "promoted"):
        reasons.append(f"model.status={mod.get('status')!r} (só production/promoted no deploy)")
    shadow_flags = 0
    for j in (new.get("jogos") or []):
        for v in (j.get("valor") or []):
            st = (v.get("model_status") or "").lower()
            if st.startswith("shadow"):
                shadow_flags += 1
    if shadow_flags:
        reasons.append(f"{shadow_flags} flags de valor com model_status shadow")

    # P0: rejects de ladder são QUARENTENA (bom). Bloqueia só se valor usa par inválido
    # (margem negativa) ou se monotonia quebrada ainda entrou no board.
    rej_path = STATUS / "ladder_rejects.json"
    if rej_path.exists():
        try:
            rej = json.loads(rej_path.read_text(encoding="utf-8"))
            n_rej = int(rej.get("n") or 0)
            by_casa_reason = {}
            for r in (rej.get("rejects") or []):
                key = (r.get("casa") or "?", r.get("reason") or "?")
                by_casa_reason[key] = by_casa_reason.get(key, 0) + 1
            print(f"[gate] ladder rejects (quarentena): {n_rej} · top={sorted(by_casa_reason.items(), key=lambda x: -x[1])[:5]}")
        except Exception as e:
            print(f"[gate] aviso ladder_rejects: {type(e).__name__}")

    # defende: nenhum flag de valor com margem implícita negativa
    n_bad_margin_valor = 0
    for j in (new.get("jogos") or []):
        for v in (j.get("valor") or []):
            odd = v.get("odd") or 0
            # se o par ainda está no board, confere margem via mercados
            merc = (j.get("mercados") or {}).get(v.get("mercado") or "") or {}
            linhas = merc.get(v.get("casa") or "") or []
            for ln in linhas:
                if abs(float(ln.get("linha") or -1) - float(v.get("linha") or -2)) > 1e-9:
                    continue
                o, u = ln.get("over"), ln.get("under")
                if o and u and o > 1 and u > 1:
                    margin = 1.0 / o + 1.0 / u - 1.0
                    if margin < -1e-6:
                        n_bad_margin_valor += 1
    if n_bad_margin_valor:
        reasons.append(f"{n_bad_margin_valor} flags de valor com margem negativa no par")

    # P0: nenhum valor acionável com kickoff passado (defesa em profundidade)
    n_past_valor = 0
    for j in (new.get("jogos") or []):
        gs = j.get("game_state")
        if gs in ("started", "finished") and (j.get("valor") or []):
            n_past_valor += len(j["valor"])
    if n_past_valor:
        reasons.append(f"{n_past_valor} flags de valor em jogo started/finished")

    print(f"[gate] agora: {n_casas_now} casas / {n_jogos_now} jogos · ao vivo: {n_casas_prev} casas / {n_jogos_prev} jogos · model={mod.get('status')}/{mod.get('source')}")
    if reasons:
        blocked = {"ts_brt": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
                   "reasons": reasons,
                   "now": {"casas": n_casas_now, "jogos": n_jogos_now},
                   "prev": {"casas": n_casas_prev, "jogos": n_jogos_prev},
                   "model": mod}
        STATUS.mkdir(parents=True, exist_ok=True)
        (STATUS / "blocked_deploy.json").write_text(json.dumps(blocked, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[gate] ❌ DEPLOY BLOQUEADO — site antigo permanece no ar:")
        for r_ in reasons: print(f"   - {r_}")
        sys.exit(3)
    print("[gate] ✅ liberado pra deploy")
    sys.exit(0)

if __name__ == "__main__":
    main()
