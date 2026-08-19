# -*- coding: utf-8 -*-
"""audit_mesa_bot_parity.py — trava de regressão: juiz do mesa_bot × BOARD.valor.

Invariante: sobre o MESMO board.js, com o MESMO bundle e o MESMO gate, o juiz
tem que reproduzir os flags da Mesa 1:1 — mesmas chaves, ΔEV = 0. Qualquer
divergência aqui é bug de código/contrato (não "opinião"): os dois lados leem
exatamente as mesmas odds.

O que NÃO é falha (avisa e sai 0):
  · board em model_source ≠ candidate_pricer (rollback FORCE_LEGACY da Mesa) —
    o bot é segunda opinião nesse cenário, paridade não se aplica;
  · Mesa com ZERO flags no board inteiro e juiz achando sinais — é o cenário
    "build da Mesa falhou no flag" do brief: o bot existe justamente pra isso;
  · flag da Mesa em jogo SEM identidade publicada (caminho legado por nome) —
    o juiz não enxerga esses de propósito (contados e listados).

O que É falha (sai 1, e no Actions bloqueia o ciclo — fail-closed):
  · ΔEV ≠ 0 em chave presente dos dois lados (drift de fórmula/limiar/bundle);
  · chave só do juiz com a Mesa flaggeando normalmente (juiz inventou);
  · chave só da Mesa em jogo COM identidade (juiz deixou de ver).

Uso:
  python3 audit_mesa_bot_parity.py --url https://valor-rdu.netlify.app/data/board.js
  python3 audit_mesa_bot_parity.py --path data/board.js   # board local

Nota honesta: se um retreino trocar o bundle entre a geração do board e este
check, um ΔEV≠0 transitório é esperado (janela de minutos — o board regenera a
cada ~30min). A mensagem de erro lembra isso; ciclo seguinte se cura sozinho.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE / "mesa_bot"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fetch_board import DEFAULT_BOARD_URL, load_board  # noqa: E402
from judge import judge_board  # noqa: E402
from signals import flatten_signals  # noqa: E402

CAMPOS = ("ev_pct", "edge_pp", "nossa_prob", "odd", "mu", "p_push")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="paridade juiz × BOARD.valor")
    ap.add_argument("--url", default=None)
    ap.add_argument("--path", default=None)
    args = ap.parse_args(argv)

    board = load_board(
        url=args.url or DEFAULT_BOARD_URL,
        path=Path(args.path) if args.path else None,
    )
    jogos = board.get("jogos") or []
    print("board gerado=%s · jogos=%d" % (board.get("gerado_iso") or "?", len(jogos)))

    mesa = flatten_signals(board)
    fontes = {s.get("model_source") for s in mesa if s.get("model_source")}
    if fontes and fontes != {"candidate_pricer"}:
        print("AVISO: board em model_source=%s (rollback legado?) — paridade não se aplica; saindo 0"
              % sorted(fontes))
        return 0

    juiz = judge_board(board)
    mk = {s["key"]: s for s in mesa}
    jk = {s["key"]: s for s in juiz}

    if not mk and jk:
        print("AVISO: Mesa com 0 flags e juiz com %d — cenário 'flag da Mesa falhou'; "
              "o bot é a segunda opinião aqui. Saindo 0." % len(jk))
        return 0

    # identidade publicada por jogo (pra separar caminho legado)
    ident_por_sofa = {}
    for j in jogos:
        if isinstance(j, dict) and j.get("sofa_id") is not None:
            ident_por_sofa[str(j.get("sofa_id"))] = bool(
                j.get("home_id") is not None and j.get("comp")
            )

    erros, avisos = [], []
    overlap = set(mk) & set(jk)
    for k in sorted(overlap):
        for c in CAMPOS:
            a, b = mk[k].get(c), jk[k].get(c)
            if (a is None) != (b is None) or (
                a is not None and abs(float(a) - float(b)) > 1e-9
            ):
                erros.append("Δ%s em %s: mesa=%s juiz=%s" % (c, k, a, b))
                break

    for k in sorted(set(mk) - set(jk)):
        sofa = k.split("|", 1)[0]
        if ident_por_sofa.get(sofa):
            erros.append("só na MESA (jogo COM identidade — juiz deixou de ver): " + k)
        else:
            avisos.append("só na MESA via caminho legado (sem identidade no board): " + k)

    for k in sorted(set(jk) - set(mk)):
        erros.append("só no JUIZ (inventou chave que a Mesa não flaggeou): " + k)

    print("paridade: mesa=%d juiz=%d overlap=%d · erros=%d · avisos=%d"
          % (len(mk), len(jk), len(overlap), len(erros), len(avisos)))
    for a in avisos[:10]:
        print("  ⚠", a)
    if len(avisos) > 10:
        print("  ⚠ … +%d avisos" % (len(avisos) - 10))
    if erros:
        for e in erros[:20]:
            print("  ✗", e)
        if len(erros) > 20:
            print("  ✗ … +%d erros" % (len(erros) - 20))
        print("\nFALHOU. Se um retreino acabou de trocar o bundle, o board ainda é da "
              "versão anterior — o próximo ciclo (~30min) se cura sozinho. Persistindo, "
              "é drift real juiz×Mesa: conferir mesa_shared.py e candidate_pricer.")
        return 1
    print("OK — juiz reproduz BOARD.valor 1:1 (ΔEV=0).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
