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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def manifest_gate(dirpath):
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
    shrink = _shrink_reason(man)
    if shrink:
        return shrink
    print(f"[deploy] manifesto ok · build {str(man.get('build_id'))[:8]} · {age:.0f}min · "
          f"{len(arts)} artefatos íntegros")
    return None


def _shrink_reason(man):
    """Trava anti-encolhimento do histórico válido vs produção AO VIVO (best-effort).
    Só age quando DEPLOY_LIVE_BASE está setado e o manifesto ao vivo é legível. Um encolhimento
    legítimo (migração aprovada) é liberado por data/odds/_status/history_shrink_approved.json."""
    if not DEPLOY_LIVE_BASE:
        return None
    try:
        live = parse_manifest_text(_fetch_text(DEPLOY_LIVE_BASE.rstrip("/") + MANIFEST_REL))
    except Exception as e:
        print(f"[deploy] manifesto ao vivo indisponível ({type(e).__name__}) — pulo a trava de encolhimento")
        return None
    def vc(m):
        a = (m.get("artifacts") or {}).get("/data/history.js") or {}
        return a.get("valid_count")
    now_v, live_v = vc(man), vc(live)
    if now_v is None or live_v is None or live_v <= 0:
        return None
    eps = float(os.environ.get("HISTORY_SHRINK_EPS", "0.02"))
    if now_v < live_v * (1 - eps):
        approved = ROOT / "data" / "odds" / "_status" / "history_shrink_approved.json"
        if approved.is_file():
            print(f"[deploy] histórico encolheu ({live_v}→{now_v}) mas há migração APROVADA — libero")
            return None
        return (f"histórico VÁLIDO encolheu {live_v}→{now_v} (>{eps*100:.0f}%) sem migração aprovada "
                f"— crie data/odds/_status/history_shrink_approved.json se for intencional")
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
    _mreason = manifest_gate(DIR)
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
