# -*- coding: utf-8 -*-
"""model_freshness.py — estado do MODELO da Mesa frente ao que o RDU publica (22/09/2026).

Auditoria A01b: o bundle (data/candidate_pricer_data.json) ficou de 08/09 a 22/09 na
versão 2026-08-29 enquanto o site do RDU servia a 2026-09-21. O candidate_pricer
calculava model_version/model_age_days só como metadado e o build_board gravava
"actionable": True sem olhar o modelo — replay do board de 22/09: com o bundle certo,
4 dos 16 sinais somem e o EV dos outros cai ~pela metade.

Estados (um por sinal):
  current     — manifesto do RDU lido e mesma versão do bundle;
  behind_rdu  — manifesto lido e o RDU numa versão MAIS NOVA. Selo + penalidade;
                vira BLOQUEIO (model_gate="block", actionable False) só depois de
                MODEL_BEHIND_BLOCK_H horas de atraso contadas pela própria Mesa
                (`behind.since`, persistido em data/odds/_status/model_freshness.json);
  stale_age   — dado do modelo com mais de MODEL_MAX_AGE_DAYS dias na liga do sinal
                (selo + penalidade, nunca bloqueio);
  unverified  — manifesto ilegível (rede, proteção de URLs ligada), bundle à frente
                do RDU ou sha divergente na mesma versão: só aviso, nunca bloqueio.
Política de produto em mesa_shared.py (o mesa_bot lê a mesma decisão do board).
Escapes: MODEL_GATE=off (nunca bloqueia) e MODEL_FROZEN_OK=<versão> (congelamento
deliberado daquela versão, explícito no valor.yml).
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mesa_shared import MODEL_BEHIND_BLOCK_H, MODEL_MAX_AGE_DAYS, RDU_MANIFEST_URL

BRT = timezone(timedelta(hours=-3))
STATE_FILE = "model_freshness.json"
PREFIXO = "window.MODELS_MANIFEST"


def parse_manifest(text):
    """Dict do manifesto publicado, ou None (página de login/HTML/JSON quebrado)."""
    t = (text or "").lstrip()
    if not t.startswith(PREFIXO):
        return None
    try:
        obj = json.JSONDecoder().raw_decode(t[t.index("{"):])[0]
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and obj.get("candidates_version") else None


def fetch_manifest(url=None, timeout=10):
    """(manifesto|None, erro|None). Uma leitura por ciclo; nunca levanta."""
    url = url or os.environ.get("RDU_MANIFEST_URL") or RDU_MANIFEST_URL
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                                   "User-Agent": "mesa-model-freshness/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            man = parse_manifest(r.read().decode("utf-8", "replace"))
        return (man, None) if man else (None, "manifest_unreadable")
    except Exception as exc:  # noqa: BLE001 — qualquer falha = "não verificado", nunca bloqueio
        return None, type(exc).__name__


def _ts(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=BRT)


def load_state(status_dir):
    try:
        return json.loads((Path(status_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(status_dir, fr):
    p = Path(status_dir)
    if not p.is_dir():
        return
    tmp = p / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p / STATE_FILE)


def assess(bundle, manifest, now, prev=None, error=None, env=None):
    """Estado GLOBAL do bundle frente ao RDU. Puro (relógio e manifesto injetados)."""
    env = os.environ if env is None else env
    prev = prev or {}
    bv = str((bundle or {}).get("version") or "")
    rv = str((manifest or {}).get("candidates_version") or "") or None
    fr = {"schema": 1, "checked_at": now.isoformat(timespec="seconds"), "bundle_version": bv,
          "bundle_generated_at": (bundle or {}).get("generated_at"), "rdu_version": rv,
          "rdu_manifest_generated_at": (manifest or {}).get("generated_at"),
          "manifest_ok": manifest is not None, "state": "current", "reason": None,
          "hours_behind": None, "block": False, "behind": None}
    antigo = prev.get("behind") or None
    if manifest is None:
        fr.update(state="unverified", reason=error or "manifest_unreadable")
        fr["behind"] = antigo            # rede caiu: o relógio do atraso NÃO zera
    elif rv == bv:
        bs, ms = (bundle or {}).get("data_sha256"), manifest.get("data_sha256")
        if bs and ms and bs != ms:
            fr.update(state="unverified", reason="same_version_sha_mismatch")
    elif bv and rv and bv < rv:
        mesmo = antigo and antigo.get("bundle_version") == bv and antigo.get("rdu_version") == rv
        since = _ts(antigo.get("since")) if mesmo else None
        since = since or now
        horas = max(0.0, (now - since).total_seconds() / 3600)
        fr.update(state="behind_rdu", reason="rdu_publishes_newer_version", hours_behind=round(horas, 2),
                  behind={"bundle_version": bv, "rdu_version": rv, "since": since.isoformat(timespec="seconds")},
                  block=horas > MODEL_BEHIND_BLOCK_H)
    else:
        fr.update(state="unverified", reason="bundle_ahead_of_rdu")
    if fr["block"] and str(env.get("MODEL_FROZEN_OK") or "") == bv:
        fr.update(block=False, reason="frozen_ok")
    if fr["block"] and str(env.get("MODEL_GATE") or "").lower() == "off":
        fr.update(block=False, reason="model_gate_off")
    return fr


def signal_state(fr, model_age_days):
    """(model_state, model_gate) de UM sinal: gate em ok | warn | block."""
    fr = fr or {}
    if fr.get("state") == "behind_rdu":
        return "behind_rdu", ("block" if fr.get("block") else "warn")
    try:
        if model_age_days is not None and float(model_age_days) > MODEL_MAX_AGE_DAYS:
            return "stale_age", "warn"
    except (TypeError, ValueError):
        pass
    if fr.get("state") == "unverified" or not fr:
        return "unverified", "warn"
    return "current", "ok"
