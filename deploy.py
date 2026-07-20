# -*- coding: utf-8 -*-
"""deploy.py — publica a pasta valor/ no site valor-rdu (Netlify) via API REST.
Token do env NETLIFY_TOKEN (GitHub Actions) ou de netlify_config.json (teste local)."""
import sys, os, json, hashlib, re, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ROOT = Path(__file__).resolve().parent
TOKEN = os.environ.get("NETLIFY_TOKEN")
if not TOKEN:
    for p in (ROOT / "netlify_config.json", ROOT.parent / "netlify_config.json"):
        if p.exists(): TOKEN = json.loads(p.read_text(encoding="utf-8"))["token"]; break
SITE_ID = "4059d137-0164-45da-a159-9f675f25600a"   # valor-rdu
DIR = ROOT / "valor"
EXCLUDE = (".bak", ".DS_Store", "Thumbs.db", ".lock", "~")
CRITICAL_FILES = {
    "/index.html",
    "/data/board.js",
    "/data/history.js",
    "/data/moves.js",
    "/data/ops.js",
    "/js/board.js",
    "/js/valor.js",
    "/js/history.js",
    "/js/ops.js",
}

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
