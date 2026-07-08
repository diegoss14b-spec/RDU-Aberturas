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

def api(method, path, data=None, raw=False):
    body, ctype = (data, "application/octet-stream") if raw else (
        (json.dumps(data).encode() if data is not None else None), "application/json")
    req = urllib.request.Request("https://api.netlify.com/api/v1" + path, data=body, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())

def main():
    if not TOKEN:
        print("❌ sem NETLIFY_TOKEN"); return 1
    files = {}
    for p in DIR.rglob("*"):
        if p.is_file() and not any(x in p.name for x in EXCLUDE):
            rel = "/" + str(p.relative_to(DIR)).replace("\\", "/")
            files[rel] = (hashlib.sha1(p.read_bytes()).hexdigest(), p)
    digest = {rel: sha for rel, (sha, _) in files.items()}
    dep = api("POST", f"/sites/{SITE_ID}/deploys", {"files": digest, "draft": False})
    required = set(dep.get("required", []))
    print(f"[valor] deploy {dep['id'][:12]} · {len(required)} a subir")
    up = 0
    for rel, (sha, p) in files.items():
        if sha not in required: continue
        for tent in range(3):
            try: api("PUT", f"/deploys/{dep['id']}/files{urllib.parse.quote(rel, safe='/')}", p.read_bytes(), raw=True); up += 1; break
            except Exception:
                if tent < 2: time.sleep(2)
    print(f"[valor] {up} subidos · aguardando ready…")
    for _ in range(40):
        d = api("GET", f"/deploys/{dep['id']}")
        if d.get("state") == "ready":
            print(f"✅ PUBLICADO: {d.get('ssl_url') or d.get('url')}"); return 0
        if d.get("state") == "error":
            print(f"❌ erro: {d.get('error_message')}"); return 1
        time.sleep(3)
    return 0

if __name__ == "__main__":
    sys.exit(main())
