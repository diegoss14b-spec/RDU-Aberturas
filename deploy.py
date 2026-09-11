# -*- coding: utf-8 -*-
"""deploy.py — publica a pasta valor/ no site valor-rdu (Netlify) via API REST.
Token do env NETLIFY_TOKEN (GitHub Actions) ou de netlify_config.json (teste local)."""
import sys, os, json, hashlib, re, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from history_quality import parse_iso_flex  # parser único §10
from history_policy import contract as history_contract, counts as history_counts, transition_report, validate_preservation
from manifest_common import (
    MANIFEST_PREFIX, MANIFEST_REL, parse_manifest_text, sha256_bytes, strip_window,
)
TOKEN = os.environ.get("NETLIFY_TOKEN")
if not TOKEN:
    for p in (ROOT / "netlify_config.json", ROOT.parent / "netlify_config.json"):
        if p.exists(): TOKEN = json.loads(p.read_text(encoding="utf-8"))["token"]; break
SITE_ID = "4059d137-0164-45da-a159-9f675f25600a"   # valor-rdu
DIR = ROOT / "valor"
EXCLUDE = (".bak", ".DS_Store", "Thumbs.db", ".lock", "~")
BRT = timezone(timedelta(hours=-3))
# idade máxima do manifesto no deploy (min) — build velho não publica
MANIFEST_MAX_AGE_MIN = float(os.environ.get("MANIFEST_MAX_AGE_MIN", "360"))
# base pública p/ comparar encolhimento do histórico válido (só no workflow); None = pula
DEPLOY_LIVE_BASE = os.environ.get("DEPLOY_LIVE_BASE") or None
CRITICAL_FILES = {
    "/index.html",
    "/data/board.js",
    "/data/history.js",
    "/data/moves.js",
    "/data/ops.js",
    "/data/openclose.js",   # §8: openclose é artefato crítico (não mais best-effort)
    "/data/manifest.js",    # §8: manifesto atômico do build
    "/js/board.js",
    "/js/valor.js",
    "/js/history.js",
    "/js/ops.js",
}


def _fetch_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "rdu-deploy/1.0",
                                               "Cache-Control": "no-cache"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except (OSError, TimeoutError) as exc:
            # Retry only transport/transient HTTP errors; never bypass the baseline.
            retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in (408, 429, 500, 502, 503, 504)
            if not retryable or attempt == 2:
                raise
            print(f"[deploy] leitura pública: {_read_error(exc)}; nova tentativa {attempt + 2}/3")
            time.sleep(attempt + 1)


def _read_error(exc):
    """Operational diagnosis without leaking URLs, response bodies or credentials."""
    return f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__


def _coherent_live_history(live_raw):
    """Read manifest/history from one generation, retrying concurrent publication.

    No fallback to an unverified local baseline, even after repeated failures.
    """
    base = DEPLOY_LIVE_BASE.rstrip("/")
    for attempt in range(3):
        live = parse_manifest_text(live_raw.decode("utf-8"))
        meta = ((live.get("artifacts") or {}).get("/data/history.js") or {})
        history_raw = _fetch_text(base + "/data/history.js").encode("utf-8")
        after_raw = _fetch_text(base + MANIFEST_REL).encode("utf-8")
        if live_raw == after_raw and sha256_bytes(history_raw) == meta.get("sha256"):
            return live, live_raw, meta, history_raw
        if attempt < 2:
            print("[deploy] publicação concorrente/cache divergente; relendo baseline completo")
            live_raw = after_raw
            time.sleep(attempt + 1)
    raise ValueError("live history hash differs from manifest or publication changed after 3 coherent reads")


def manifest_gate(dirpath, *, history_root=None):
    """§8 — bloqueia se o manifesto estiver ausente/inválido/velho, se algum artefato
    do build faltar ou tiver hash diferente (build misturado), ou se o histórico VÁLIDO
    encolher em relação à produção sem migração aprovada. Retorna None (ok) ou motivo."""
    base = Path(dirpath)
    mpath = base / MANIFEST_REL.lstrip("/")
    if not mpath.is_file():
        return "manifesto ausente (build não atômico) — rode build_manifest.py"
    try:
        man = parse_manifest_text(mpath.read_text(encoding="utf-8"))
    except Exception as e:
        return f"manifesto ilegível: {type(e).__name__}: {e}"
    gi = man.get("generated_iso")
    dt = parse_iso_flex(gi, default_tz=BRT)
    if dt is None:
        return f"manifesto sem generated_iso válido ({gi!r})"
    age = (datetime.now(BRT) - dt.astimezone(BRT)).total_seconds() / 60.0
    if age > MANIFEST_MAX_AGE_MIN:
        return f"manifesto velho: {age:.0f}min > {MANIFEST_MAX_AGE_MIN:.0f}min (build defasado)"
    arts = man.get("artifacts") or {}
    if not arts:
        return "manifesto sem artefatos"
    for rel, meta in arts.items():
        f = base / rel.lstrip("/")
        if not f.is_file():
            return f"artefato do manifesto ausente no build: {rel}"
        got = sha256_bytes(f.read_bytes())
        if got != meta.get("sha256"):
            return (f"hash divergente em {rel} — artefato de OUTRO build "
                    f"(manifesto {str(meta.get('sha256'))[:12]} ≠ arquivo {got[:12]})")
        if rel == "/data/history.js":
            try:
                history = strip_window(f.read_text(encoding="utf-8"), "window.HIST=")
                contract = history_contract(history, got)
                if meta.get("history_contract") != contract:
                    return "contrato da política CLV diverge do artefato vinculado"
                if contract is not None and (meta.get("valid_count") != contract["counts"]["clv_validas"] or
                                             meta.get("count") != contract["counts"]["liquidadas"]):
                    return "contagens do manifesto divergem do contrato CLV"
                if contract is not None:
                    validate_preservation(contract.get("preservation"))
                    if history_root is not None:
                        from history_preservation_inventory import validate_local_inventory
                        proof = validate_local_inventory(contract["preservation"], history_root)
                        print(f"[deploy] histórico real conferido: {proof['raw_count']} registros; "
                              f"{proof['settled_count']} liquidados; "
                              f"{proof.get('remapped_raw_count', 0)} identidades preservadas por renomeação")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return "política CLV inválida: " + str(exc)
    shrink = _shrink_reason(man)
    if shrink:
        return shrink
    print(f"[deploy] manifesto ok · build {str(man.get('build_id'))[:8]} · {age:.0f}min · "
          f"{len(arts)} artefatos íntegros")
    return None


def _shrink_reason(man):
    """Preserve raw history; one closed legacy→v1 eligibility transition only.

    The legacy approval-file bypass is intentionally no longer recognized.
    New-policy deployments fail closed if the live baseline cannot be verified.
    """
    if not DEPLOY_LIVE_BASE:
        return None
    target = ((man.get("artifacts") or {}).get("/data/history.js") or {}).get("history_contract")
    try:
        live_raw = _fetch_text(DEPLOY_LIVE_BASE.rstrip("/") + MANIFEST_REL).encode("utf-8")
        live = parse_manifest_text(live_raw.decode("utf-8"))
    except Exception as e:
        if target is not None:
            return "manifesto ao vivo indisponível; política CLV exige baseline verificável (" + _read_error(e) + ")"
        print(f"[deploy] manifesto ao vivo indisponível ({type(e).__name__}) — pulo a trava de encolhimento")
        return None
    live_meta = ((live.get("artifacts") or {}).get("/data/history.js") or {})
    if target is not None or live_meta.get("history_contract") is not None:
        try:
            live, live_raw, live_meta, live_history_raw = _coherent_live_history(live_raw)
            live_history = strip_window(live_history_raw.decode("utf-8"), "window.HIST=")
            previous = history_contract(live_history, sha256_bytes(live_history_raw))
            if previous != live_meta.get("history_contract"):
                raise ValueError("live CLV contract differs from hash-bound artifact")
            if target is None:
                raise ValueError("cannot roll back strict CLV policy to unqualified legacy")
            before, after = history_counts(live_history), target["counts"]
            if any(after[k] < before[k] for k in ("monitoradas", "liquidadas")):
                raise ValueError("raw/settled history count fell")
            if previous is None:
                report = transition_report(target, live_history, live_raw, live_history_raw, man.get("build_id"))
                print("[deploy] CLV_POLICY_TRANSITION " + json.dumps(report, ensure_ascii=False, sort_keys=True))
                return None
            if previous.get("policy") != target.get("policy"):
                raise ValueError("unsupported CLV policy transition")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return "transição/integridade CLV bloqueada: " + str(exc)
    def vc(m):
        a = (m.get("artifacts") or {}).get("/data/history.js") or {}
        return a.get("valid_count")
    now_v, live_v = vc(man), vc(live)
    if now_v is None or live_v is None or live_v <= 0:
        return None
    eps = float(os.environ.get("HISTORY_SHRINK_EPS", "0.02"))
    if now_v < live_v * (1 - eps):
        return f"histórico VÁLIDO encolheu {live_v}→{now_v} (>{eps*100:.0f}%) fora da transição fechada de política"
    return None

def api(method, path, data=None, raw=False):
    body, ctype = (data, "application/octet-stream") if raw else (
        (json.dumps(data).encode() if data is not None else None), "application/json")
    req = urllib.request.Request("https://api.netlify.com/api/v1" + path, data=body, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())

def cachebust(html_bytes, base):
    """P1.4 — adiciona ?v=<sha8> aos scripts locais js/*.js e data/*.js (tags estáticas E os
    caminhos injetados dinamicamente pelo lazy-load), pra o browser buscar a versão nova quando
    o arquivo muda. Só reescreve o index.html no upload — o arquivo-fonte fica limpo."""
    txt = html_bytes.decode("utf-8")
    def repl(m):
        q, path = m.group(1), m.group(2)
        f = base / path
        if not f.is_file():
            return m.group(0)
        h = hashlib.sha1(f.read_bytes()).hexdigest()[:8]
        return q + path + "?v=" + h + q
    # casa em src="js/x.js" e em inject("data/history.js", ...) — sempre entre aspas, nunca em comentário
    return re.sub(r'(["\'])((?:js|data)/[A-Za-z0-9_.\-]+\.js)\1', repl, txt).encode("utf-8")


def main():
    if not TOKEN:
        print("❌ sem NETLIFY_TOKEN")
        return 1
    # Guard anti-stub (P0.1, 18/07): nunca publicar um diretório que não seja o app completo
    # da Mesa (4 views + index substancial). Em 20/07 o STUB de /Claude/valor foi publicado
    # por cima do site pela rota legada — esta é a última linha de defesa no deploy correto.
    _views = [DIR / "js" / (v + ".js") for v in ("board", "valor", "history", "ops")]
    _idx = DIR / "index.html"
    if not (_idx.is_file() and _idx.stat().st_size > 15000 and all(v.is_file() for v in _views)):
        print("❌ ABORTADO — esta pasta NÃO é o app completo da Mesa (parece um STUB).")
        print("   pasta alvo: " + str(DIR))
        return 1
    # §8 — publicação atômica: board/ops/history/moves/openclose têm que ser do MESMO build,
    # frescos e íntegros. Bloqueia build misturado/defasado ANTES de tocar a produção.
    _mreason = manifest_gate(DIR, history_root=ROOT / "data" / "odds_history")
    if _mreason:
        print("❌ ABORTADO — manifesto/atômico: " + _mreason)
        return 1
    files = {}
    for p in DIR.rglob("*"):
        if p.is_file() and not any(x in p.name for x in EXCLUDE):
            rel = "/" + str(p.relative_to(DIR)).replace("\\", "/")
            data = p.read_bytes()
            if p.name == "index.html":
                data = cachebust(data, DIR)   # P1.4 — assets js com ?v=<hash> (mata cache velho)
            files[rel] = (hashlib.sha1(data).hexdigest(), data)

    missing_local = sorted(CRITICAL_FILES - set(files))
    if missing_local:
        print("❌ arquivos críticos ausentes: " + ", ".join(missing_local))
        return 1

    digest = {rel: sha for rel, (sha, _) in files.items()}
    dep = api("POST", f"/sites/{SITE_ID}/deploys", {"files": digest, "draft": False})
    deploy_id = dep.get("id")
    if not deploy_id:
        print("❌ Netlify não retornou o id do deploy")
        return 1

    required = set(dep.get("required", []))
    print(f"[valor] deploy {deploy_id[:12]} · {len(required)} hashes a subir")
    uploaded = set()
    failures = []
    for rel, (sha, data) in files.items():
        if sha not in required:
            continue
        last_error = None
        for attempt in range(3):
            try:
                api(
                    "PUT",
                    f"/deploys/{deploy_id}/files{urllib.parse.quote(rel, safe='/')}",
                    data,
                    raw=True,
                )
                uploaded.add(sha)
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2)
        else:
            failures.append((rel, str(last_error)))

    missing_uploads = sorted(required - uploaded)
    if failures or missing_uploads:
        for rel, error in failures:
            print(f"❌ upload falhou: {rel}: {error}")
        if missing_uploads:
            print(f"❌ {len(missing_uploads)} hashes exigidos não foram enviados")
        return 1

    print(f"[valor] {len(uploaded)} hashes subidos · aguardando ready…")
    for _ in range(40):
        d = api("GET", f"/deploys/{deploy_id}")
        if d.get("state") == "ready":
            print(f"✅ PUBLICADO: {d.get('ssl_url') or d.get('url')}")
            return 0
        if d.get("state") == "error":
            print(f"❌ erro: {d.get('error_message')}")
            return 1
        time.sleep(3)

    print("❌ timeout: Netlify não confirmou o deploy como ready")
    return 1
if __name__ == "__main__":
    sys.exit(main())
