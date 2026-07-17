# -*- coding: utf-8 -*-
"""deploy.py — publica a pasta valor/ no site valor-rdu (Netlify) via API REST.
Token do env NETLIFY_TOKEN (GitHub Actions) ou de netlify_config.json (teste local)."""
import sys, os, json, hashlib, time, urllib.request, urllib.parse, urllib.error
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

def main():
    if not TOKEN:
        print("❌ sem NETLIFY_TOKEN")
        return 1
    files = {}
    for p in DIR.rglob("*"):
        if p.is_file() and not any(x in p.name for x in EXCLUDE):
            rel = "/" + str(p.relative_to(DIR)).replace("\\", "/")
            files[rel] = (hashlib.sha1(p.read_bytes()).hexdigest(), p)

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
    for rel, (sha, p) in files.items():
        if sha not in required:
            continue
        last_error = None
        for attempt in range(3):
            try:
                api(
                    "PUT",
                    f"/deploys/{deploy_id}/files{urllib.parse.quote(rel, safe='/')}",
                    p.read_bytes(),
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
