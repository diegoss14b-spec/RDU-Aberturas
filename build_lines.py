# -*- coding: utf-8 -*-
"""build_lines.py — HISTÓRICO DA LINHA (não da odd) por jogo/mercado/casa.

Responde "a linha de faltas abriu 25,5 na quarta e fechou 23,5 no sábado".

O explorador que já existia (moves.js) desenha a ODD de UMA linha ao longo do tempo.
Isso não responde a pergunta acima: quando a casa move a linha de 25,5 pra 24,5, a
série de 25,5 simplesmente para e outra começa. Aqui a série é da LINHA PRINCIPAL —
a mais equilibrada do mercado naquele instante, pelo MESMO `pick_main_line` que o
ingest usa pra decidir o que é movimento de linha.

⚠️ A reconstrução sai dos TICKS de preço, não dos ticks `line_move`. Dois motivos:
  1. retroativa — vale pra todo o histórico já capturado, inclusive antes de existir
     qualquer tick de linha;
  2. completa — o `line_move` só nasce a partir do 2º valor, então mercado cuja linha
     nunca se mexeu não teria série nenhuma (e "abriu em 25,5 e não mexeu" é resposta
     legítima). O `line_open` (03/08) fecha essa lacuna dali pra frente, mas a série
     daqui não depende dele.

Saída: valor/data/lines.js -> window.LINES = {built, games:{gid:{...}}, s:{gid:{mercado:{casa:[[t,linha,over,under],...]}}}}
`t` é minuto epoch (mesma unidade do moves.js). Só ponto PRÉ-KICKOFF, e só quando a
linha principal MUDA (o 1º ponto é sempre a abertura).
"""
import glob
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import norm_team, parse_history_key, load_sofa_fixtures
from history_merge import merge_records
from history_quality import CLOSE_EPS, ensure_aware, parse_ts, pick_main_line
from migrate_history_keys import unify_keys_dict

OUT = ROOT / "valor" / "data" / "lines.js"
# mesma lista do build_moves: mercados de Mais/Menos com par over/under. O handicap
# de cartões fica FORA porque "linha principal" ali é outra coisa (mando, não total).
BOARD_M = {"Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais",
           "Tiros de meta", "Escanteios", "Chutes no gol", "Desarmes"}
TOL_JANELA = 90   # min de folga na janela de oferta da linha (captura não é fixa)


def parsed(v):
    return ensure_aware(parse_ts(v))


def emin(dt):
    return int(dt.timestamp() // 60) if dt else None


def valid_odd(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return 1.0 < f <= 50.0


def prematch(dt, ko):
    return bool(dt and (not ko or dt < ko - CLOSE_EPS))


def load_keys():
    docs = {}
    for p in sorted(glob.glob(str(ROOT / "data/odds_history/keys/*.json"))):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, rec in d.items():
            if k.startswith("__") or not isinstance(rec, dict):
                continue
            docs[k] = merge_records(docs[k], rec) if k in docs else rec
    return docs


def main():
    keys = load_keys()
    _, gid_alias, _ = unify_keys_dict(dict(keys))

    # sofa_id -> nome bonito + liga (só cobre a janela corrente; o resto usa o cru)
    fx = {}
    for f in load_sofa_fixtures():
        if f.get("sofa_id"):
            fx[str(f["sofa_id"])] = f

    # alias (dia, home_norm, away_norm) -> sofa_id, igual ao build_moves
    aliases = {}
    meta_gid = {}
    for k, rec in keys.items():
        m = parse_history_key(k)
        sid = rec.get("sofa_id") or m.get("sofa_id")
        day = m.get("day") or (rec.get("kickoff") or "")[:10]
        hn = norm_team(rec.get("home_norm") or rec.get("home_raw") or m.get("hn") or "")
        an = norm_team(rec.get("away_norm") or rec.get("away_raw") or m.get("an") or "")
        if sid and day and hn and an:
            aliases[(day, hn, an)] = str(sid)
            aliases[(day, an, hn)] = str(sid)
        gid = f"sofa:{sid}" if sid else f"{day}|{hn}|{an}"
        gid = gid_alias.get(gid, gid)
        info = meta_gid.setdefault(gid, {"h": "", "a": "", "ko": "", "sid": sid})
        if rec.get("home_raw") and not info["h"]:
            info["h"] = rec["home_raw"]
        if rec.get("away_raw") and not info["a"]:
            info["a"] = rec["away_raw"]
        if rec.get("kickoff") and not info["ko"]:
            info["ko"] = rec["kickoff"]

    # ⚠️ LINHAS LEGÍTIMAS vêm das CHAVES, não dos ticks. A limpeza das chaves
    # contaminadas (mercado de TIME gravado no espaço do mercado de JOGO) foi feita
    # sobre `keys/`; os `ticks/` NÃO foram tocados e ainda carregam as linhas velhas.
    # Exemplo medido: América MG × Londrina 13/07, superbet/Finalizações — os ticks de
    # 11-12/07 trazem 10,5/11,5/15,5/16,5 (chutes de UM time) junto com 26,5/27,5/28,5
    # (jogo inteiro). Sem esta trava a "linha principal" oscila entre as duas escadas e
    # o produto anuncia um movimento de 16,5 → 10,5 que nunca existiu.
    linhas_ok = defaultdict(set)
    janela = {}
    for k, rec in keys.items():
        m = parse_history_key(k)
        if m.get("mercado") not in BOARD_M:
            continue
        sid = rec.get("sofa_id") or m.get("sofa_id")
        day = m.get("day") or (rec.get("kickoff") or "")[:10]
        hn = norm_team(rec.get("home_norm") or rec.get("home_raw") or m.get("hn") or "")
        an = norm_team(rec.get("away_norm") or rec.get("away_raw") or m.get("an") or "")
        gid = gid_alias.get(f"sofa:{sid}" if sid else f"{day}|{hn}|{an}",
                            f"sofa:{sid}" if sid else f"{day}|{hn}|{an}")
        try:
            gk = (gid, m["mercado"], m.get("casa") or k.split("|")[0])
            lf = float(m["linha"])
        except (TypeError, ValueError, KeyError):
            continue
        linhas_ok[gk].add(lf)
        # JANELA DE OFERTA da linha: `last_ts` é atualizado a cada observação
        # (não só quando o preço muda), então é o instante em que a casa ainda
        # oferecia aquela linha. Sem isto o estado carrega linha aposentada pra
        # sempre e a "principal" congela: quando a casa RETIRA a 25,5 da escada,
        # a principal anda de verdade — e retirada não gera tick nenhum, porque
        # tick é registro de MUDANÇA, não de fotografia.
        ab, fe = emin(parsed(rec.get("open_ts"))), emin(parsed(rec.get("last_ts")))
        ini, fim = janela.get((gk, lf), (None, None))
        if ab is not None:
            ini = ab if ini is None else min(ini, ab)
        if fe is not None:
            fim = fe if fim is None else max(fim, fe)
        janela[(gk, lf)] = (ini, fim)

    # eventos de preço: (gid, mercado, casa) -> {t -> {linha -> {over,under}}}
    ev = defaultdict(lambda: defaultdict(dict))
    kicks = {}
    n_tick = n_fora = 0
    for p in sorted(glob.glob(str(ROOT / "data/odds_history/ticks/*.jsonl"))):
        with open(p, encoding="utf-8") as fh:
            for raw in fh:
                try:
                    t = json.loads(raw)
                except Exception:
                    continue
                if t.get("mercado") not in BOARD_M:
                    continue
                lado = t.get("lado")
                if lado not in ("over", "under") or not valid_odd(t.get("odd")):
                    continue
                dt, ko = parsed(t.get("ts")), parsed(t.get("kickoff"))
                if not prematch(dt, ko):
                    continue
                day = t.get("djogo") or (t.get("kickoff") or "")[:10]
                hn, an = norm_team(t.get("home") or ""), norm_team(t.get("away") or "")
                sid = t.get("sofa_id") or aliases.get((day, hn, an))
                gid = gid_alias.get(f"sofa:{sid}" if sid else f"{day}|{hn}|{an}",
                                    f"sofa:{sid}" if sid else f"{day}|{hn}|{an}")
                tm = emin(dt)
                if tm is None:
                    continue
                gk = (gid, t.get("mercado"), t.get("casa"))
                ok = linhas_ok.get(gk)
                try:
                    lf = float(t.get("linha"))
                except (TypeError, ValueError):
                    continue
                # série sem chave nenhuma (só tick) segue aceita — a trava é contra
                # linha que a limpeza JÁ removeu, não contra série ainda não indexada
                if ok and lf not in ok:
                    n_fora += 1
                    continue
                ev[gk][tm].setdefault(lf, {})[lado] = float(t["odd"])
                if ko:
                    kicks[gk] = emin(ko)
                n_tick += 1

    # a abertura de cada chave também é um ponto (tick de open pode ter ficado num
    # arquivo de dia já podado; a chave guarda open_odd/open_ts pra sempre)
    for k, rec in keys.items():
        m = parse_history_key(k)
        mercado, linha, lado = m.get("mercado"), m.get("linha"), m.get("lado")
        if mercado not in BOARD_M or linha is None or lado not in ("over", "under"):
            continue
        ko = parsed(rec.get("kickoff"))
        sid = rec.get("sofa_id") or m.get("sofa_id")
        day = m.get("day") or (rec.get("kickoff") or "")[:10]
        hn = norm_team(rec.get("home_norm") or rec.get("home_raw") or m.get("hn") or "")
        an = norm_team(rec.get("away_norm") or rec.get("away_raw") or m.get("an") or "")
        gid = f"sofa:{sid}" if sid else f"{day}|{hn}|{an}"
        gid = gid_alias.get(gid, gid)
        gk = (gid, mercado, m.get("casa") or k.split("|")[0])
        for ts_f, odd_f in (("open_ts", "open_odd"), ("close_ts", "close_odd"),
                            ("last_ts", "last_odd")):
            dt = parsed(rec.get(ts_f))
            if valid_odd(rec.get(odd_f)) and prematch(dt, ko):
                tm = emin(dt)
                if tm is not None:
                    ev[gk][tm].setdefault(linha, {})[lado] = float(rec[odd_f])
        if ko:
            kicks[gk] = emin(ko)

    # caminhada temporal: carrega o estado e reavalia a linha principal
    out = defaultdict(lambda: defaultdict(dict))
    n_series = n_pt = 0
    for (gid, mercado, casa), por_t in ev.items():
        ko = kicks.get((gid, mercado, casa))
        estado, serie, ult = {}, [], None
        for tm in sorted(por_t):
            if ko is not None and tm >= ko:
                continue
            for linha, lados in por_t[tm].items():
                try:
                    lf = float(linha)
                except (TypeError, ValueError):
                    continue
                estado.setdefault(lf, {})
                estado[lf].update(lados)
            # só entram no cálculo as linhas que a casa AINDA oferecia em `tm`
            # (janela [open_ts, last_ts] da chave; TOL_JANELA absorve o intervalo
            # entre capturas, que não é fixo)
            vivas = []
            for ln, v in estado.items():
                ini, fim = janela.get(((gid, mercado, casa), ln), (None, None))
                if ini is not None and tm < ini - TOL_JANELA:
                    continue
                if fim is not None and tm > fim + TOL_JANELA:
                    continue
                vivas.append({"linha": ln, "over": v.get("over"), "under": v.get("under")})
            main = pick_main_line(vivas)
            if main is None:
                continue
            cur = estado.get(main) or {}
            ponto = [tm, main, cur.get("over"), cur.get("under")]
            if ult is None or abs(main - ult[1]) >= 0.01:
                serie.append(ponto)     # mudou de linha → registra
                ult = ponto
            else:
                ult[0], ult[2], ult[3] = tm, cur.get("over"), cur.get("under")
        if not serie:
            continue
        # o último ponto vale como fechamento observado: reanexa se a linha ficou
        # parada depois da última mudança (senão o "fechou em X" some da tela)
        if ult is not None and serie[-1] is not ult:
            serie.append(ult)
        out[gid][mercado][casa] = serie
        n_series += 1
        n_pt += len(serie)

    games = {}
    for gid in out:
        info = meta_gid.get(gid) or {}
        sid = str(info.get("sid") or "")
        f = fx.get(sid) or {}
        games[gid] = {
            "h": f.get("home") or info.get("h") or "?",
            "a": f.get("away") or info.get("a") or "?",
            "ko": info.get("ko") or f.get("start_utc") or "",
            "lg": f.get("league") or "",
        }

    payload = {"built": datetime.now().isoformat(timespec="seconds"),
               "games": games, "s": {g: dict(m) for g, m in out.items()}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.LINES=" + json.dumps(payload, ensure_ascii=False,
                                                separators=(",", ":")) + ";",
                   encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    movi = sum(1 for g in out.values() for m in g.values() for s in m.values() if len(s) > 1)
    print(f"[lines] {n_tick:,} ticks · {len(games):,} jogos · {n_series:,} séries "
          f"({movi:,} com movimento de linha) · {n_pt:,} pontos · {kb:,.0f} KB")
    print(f"[lines] {n_fora:,} ticks descartados por linha fora da escada limpa "
          f"(contaminação de mercado de time nos ticks antigos)")


if __name__ == "__main__":
    main()
