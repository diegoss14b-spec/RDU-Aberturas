# -*- coding: utf-8 -*-
"""smoke_valor_rdu.py — smoke test da Mesa de Aberturas (anti-stub + integridade).

Valida que o que está publicado (ou prestes a ser publicado) é o APP COMPLETO:
index com as 4 views, JS essenciais respondendo, BOARD/OPS parseáveis e board
dentro da idade esperada. Detecta o STUB (index ~7KB, 1 aba, board de 10/07) que
foi publicado por cima do site em 20/07/2026 pela rota legada.

Uso:
  python scripts/smoke_valor_rdu.py https://valor-rdu.netlify.app          # produção
  python scripts/smoke_valor_rdu.py http://localhost:8123                  # staging local
  python scripts/smoke_valor_rdu.py <url> --max-age-min 240               # tolerância de idade
Sai com 0 = passou; 1 = FALHOU (não considerar o deploy bem-sucedido).
"""
import json, re, sys, time, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "rdu-smoke/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", errors="replace")

def main():
    if len(sys.argv) < 2:
        print("uso: smoke_valor_rdu.py <base_url> [--max-age-min N]"); return 1
    base = sys.argv[1].rstrip("/")
    max_age = 240.0
    if "--max-age-min" in sys.argv:
        max_age = float(sys.argv[sys.argv.index("--max-age-min") + 1])
    cb = f"smoke={int(time.time())}"
    fails = []

    # 1) index: 200 + tamanho de app completo + 4 views
    st, idx = get(f"{base}/index.html?{cb}")
    if st != 200: fails.append(f"index HTTP {st}")
    if len(idx) < 15000: fails.append(f"index só tem {len(idx)}B (assinatura de STUB: ~7KB)")
    for v in ("board", "valor", "history", "ops"):
        if f"js/{v}.js" not in idx: fails.append(f"view ausente do index: js/{v}.js")

    # 2) JS essenciais respondem 200
    for path in ("js/board.js", "js/valor.js", "js/history.js", "js/ops.js", "data/board.js", "data/ops.js"):
        try:
            st2, _ = get(f"{base}/{path}?{cb}")
            if st2 != 200: fails.append(f"{path} HTTP {st2}")
        except Exception as e:
            fails.append(f"{path} erro: {e}")

    # 3) BOARD parseável + idade
    try:
        _, braw = get(f"{base}/data/board.js?{cb}")
        board = json.loads(braw.split("=", 1)[1].strip().rstrip(";"))
        n = len(board.get("jogos") or [])
        if n < 20: fails.append(f"board com só {n} jogos (stub tinha 20 velhos; app real ~150+)")
        import datetime as dt
        stamp = board.get("gerado_iso") or board.get("gerado")
        parsed = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) if "T" in str(stamp) \
            else dt.datetime.strptime(str(stamp), "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone(dt.timedelta(hours=-3)))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=-3)))
        age = (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds() / 60
        print(f"[smoke] board: {n} jogos · gerado há {age:.0f} min")
        if age > max_age: fails.append(f"board velho: {age:.0f} min (> {max_age:.0f})")
    except Exception as e:
        fails.append(f"BOARD não parseia: {e}")

    # 4) OPS parseável
    try:
        _, oraw = get(f"{base}/data/ops.js?{cb}")
        json.loads(oraw.split("=", 1)[1].strip().rstrip(";"))
    except Exception as e:
        fails.append(f"OPS não parseia: {e}")

    if fails:
        print("❌ SMOKE FALHOU:")
        for f in fails: print(f"   - {f}")
        return 1
    print("✅ smoke ok — app completo, board fresco, dados parseáveis")
    return 0

if __name__ == "__main__":
    sys.exit(main())
