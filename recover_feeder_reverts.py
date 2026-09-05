# -*- coding: utf-8 -*-
"""recover_feeder_reverts.py — devolve à working tree o dado CRU que um commit do
feeder REVERTEU (auditoria 05/09/2026, entrega 3).

O defeito (feeder_mesa_once.py, push_verificado): push rejeitado → `fetch` →
`reset --soft origin/main` → `commit`. O index ainda era a árvore do ciclo VELHO,
então o commit novo = árvore do AVÔ + arquivos do feeder — e tudo que origin/main
tinha ganhado no meio (o snapshot da Mesa: ticks/, keys/, ledger/, _snapshots/,
ponteiros, valor/data/*.js) saía do main. 4 casos reais em 03-04/09/2026:
  feeder 6939350 desfez 8f8b7a2 · c308815 desfez 7af90eb ·
  54371aa desfez fbdb82a · 864e629 desfez 426fd27
Os commits de snapshot continuam na HISTÓRIA (são pais dos commits do feeder) —
o que se perdeu é a LINHAGEM: o tip do main não os carrega e o pipeline só lê o tip.

O que este script restaura (só o CRU; nunca commita):
  · data/odds_history/ticks/*.jsonl → MESCLA: união por assinatura lógica, ordem
    por ts, NUNCA substitui o arquivo (as linhas mais novas do main ficam).
  · data/odds/_snapshots/* e data/fixtures/_snapshots/* → arquivo imutável
    (nome = hash do conteúdo): restaura se estiver AUSENTE, nunca sobrescreve.
  · ponteiros (*_latest*.json, fixtures/sofa_latest*.json) → só se o da working
    tree for MAIS VELHO que o do snapshot (campo `at`); na prática o main já tem
    ponteiro mais novo e nada é tocado.
O que NÃO restaura, de propósito: keys/, ledger/, clv/, results/, house_event_map,
_status/, valor/data/*.js — são DERIVADOS. Copiar derivado velho por cima do atual
desfaria tudo que o pipeline ingeriu depois. Só são LISTADOS no relatório.

Modos: --dry-run (padrão) imprime o que faria; --apply grava na working tree.
Uso:  python3 recover_feeder_reverts.py --dry-run
      python3 recover_feeder_reverts.py --apply --par 6939350:8f8b7a2 ...
"""
import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from capture_common import _parse_pointer_at        # noqa: E402  (ISO ou legado YYYY-MM-DD_HHMM)
from history_merge import atomic_write_text         # noqa: E402
from migrate_history_keys import _tick_signature    # noqa: E402  (a régua de "tick idêntico" do migrate)

# Os pares provados na auditoria de 05/09/2026 (feeder_sha, snapshot_sha).
# 4 da auditoria + 2 achados pelo revisor no mesmo dia (o feeder velho seguiu rodando
# até o conserto subir): 85c7925ad desfez 1b56b71fd (05/09 10:52) e 4df728732 desfez
# 33b01599c (05/09 12:02) — juntos, +19.843 linhas de ticks de 05/09 e 2 ponteiros
# (7k/estrelabet *_latest_full.json presos num full 6 h mais velho). ⚠️ Lista fixa
# envelhece: antes de aplicar em produção, varrer `git log origin/main` de novo
# (feeder commit cuja árvore vs avô não muda caminho fora de FEEDER_PATHS).
PARES_0509 = [
    ("6939350", "8f8b7a2"),
    ("c308815", "7af90eb"),
    ("54371aa", "fbdb82a"),
    ("864e629", "426fd27"),
    ("85c7925ad", "1b56b71fd"),
    ("4df728732", "33b01599c"),
]

# Tupla SEM identidade de jogo (nem gid, nem home/away, nem sofa_id). É o 3º nível
# do dedupe: o `migrate_tick_file` re-canoniza ticks quando o fixture aparece
# (gid legado '2026-09-03|univ catolica equ|sd aucas' → 'sofa:15502671', e os nomes
# mudam junto). O snapshot revertido pode carregar a forma canônica e o main a forma
# legada da MESMA observação — e, com o fixture já fora do sofa_latest, o pipeline
# não junta mais as duas. Medido em 05/09: 14 ticks do estrelabet nessa situação.
# 05/09 (revisão): "kickoff" e "djogo" entram na tupla livre — sem eles, dois jogos com a mesma
# odd/linha no mesmo ts colidiam e o nível 3 podia admitir a duplicata de identidade divergente e
# descartar o jogo novo. Pior caso residual (mesmo kickoff, mesma linha e odd, mesmo ts) fica por ordem.
_LIVRE = ("ts", "kind", "casa", "kickoff", "djogo", "mercado", "linha", "lado", "odd", "linha_from", "linha_to")


def _tupla_livre(row):
    return tuple(row.get(f) for f in _LIVRE)


def _ordem_ts(ts):
    """Chave de ordenação por ts: datetime quando parseia, string como fallback."""
    s = str(ts or "")
    try:
        return (0, datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").timestamp(), s)
    except ValueError:
        return (1, 0.0, s)


def mesclar_ticks(base, extra):
    """União de linhas JSONL de ticks: `base` (working tree, manda) ⊎ `extra`
    (versão do snapshot revertido). Devolve (linhas_finais, stats).

    Dedupe em 3 níveis, do mais estrito ao mais largo:
      1. texto idêntico;
      2. assinatura lógica do migrate (_tick_signature) — pega `sofa_id` int×str;
      3. mesma observação sem identidade (_tupla_livre) por CONTAGEM: dentro de um
         mesmo ts (= uma rodada do ingest) cada (casa, jogo, mercado, linha, lado)
         gera exatamente 1 tick; se o main já tem tantas linhas daquela tupla
         quanto o snapshot, as sobras do snapshot são a mesma observação com outra
         identidade (legado×sofa), não dado novo.
    Linha inválida (JSON quebrado) é preservada como está e nunca deduplicada.
    Saída ordenada por ts, estável (main antes do restaurado no mesmo ts).
    """
    stats = Counter()
    base = [l for l in base if l.strip()]
    extra = [l for l in extra if l.strip()]
    exatas = set(base)
    assin = set()
    livre_main = Counter()
    parsed_base = []
    for l in base:
        try:
            r = json.loads(l)
        except ValueError:
            parsed_base.append((l, None))
            continue
        parsed_base.append((l, r))
        assin.add(_tick_signature(r))
        livre_main[_tupla_livre(r)] += 1

    # 1º passe: separa o que já está no main (nível 1/2) do que ainda é candidato.
    # Quem casa no nível 1/2 CONSOME a linha correspondente do main na contagem
    # livre — senão o nível 3 compararia "sobras do snapshot" contra "main inteiro"
    # e recusaria dado novo (main 1 linha, snapshot 2 sendo 1 já casada → 1 nova).
    candidatas = []
    livre_snap = Counter()
    for l in extra:
        if l in exatas:
            stats["ja_no_main_exato"] += 1
            try:
                livre_main[_tupla_livre(json.loads(l))] -= 1
            except ValueError:
                pass
            continue
        try:
            r = json.loads(l)
        except ValueError:
            candidatas.append((l, None))
            continue
        if _tick_signature(r) in assin:
            stats["ja_no_main_logico"] += 1
            livre_main[_tupla_livre(r)] -= 1
            continue
        livre_snap[_tupla_livre(r)] += 1
        candidatas.append((l, r))

    # nível 3: por tupla livre, só entram max(0, sobras_snapshot - sobras_main) linhas
    permitidas = {t: max(0, n - max(0, livre_main.get(t, 0))) for t, n in livre_snap.items()}
    novas = []
    vistas = set()
    for l, r in candidatas:
        if r is None:
            if l in vistas:
                continue
            vistas.add(l)
            novas.append((l, r))
            stats["novas_json_invalido"] += 1
            continue
        t = _tupla_livre(r)
        if permitidas.get(t, 0) <= 0:
            stats["ja_no_main_identidade_divergente"] += 1
            continue
        permitidas[t] -= 1
        novas.append((l, r))
        stats["novas"] += 1
        stats[f"novas:{r.get('kind')}"] += 1

    todas = parsed_base + novas
    todas.sort(key=lambda lr: _ordem_ts(lr[1].get("ts") if isinstance(lr[1], dict) else ""))
    return [l for l, _ in todas], stats


def ponteiro_mais_novo_ou_igual(texto_wt, texto_snap):
    """True se o ponteiro da working tree é tão novo ou mais novo que o do snapshot
    (ou se não dá pra comparar — na dúvida, NÃO sobrescreve)."""
    try:
        at_wt = _parse_pointer_at(json.loads(texto_wt).get("at"))
        at_sn = _parse_pointer_at(json.loads(texto_snap).get("at"))
    except (ValueError, AttributeError, TypeError):
        return True
    if at_wt is None or at_sn is None:
        return True
    return at_wt >= at_sn


# ─────────────────────────────── git ────────────────────────────────────────

def _git(root, *args, binario=False):
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout if binario else p.stdout.decode("utf-8", "replace")


def _blob(root, rev, path):
    """Hash do blob de `path` em `rev`, ou None se não existe nessa árvore."""
    p = subprocess.run(["git", "rev-parse", "--verify", "-q", f"{rev}:{path}"],
                       cwd=str(root), capture_output=True, text=True)
    return p.stdout.strip() or None


def _conteudo(root, rev, path, binario=False):
    return _git(root, "show", f"{rev}:{path}", binario=binario)


def _linhas(texto):
    return [l for l in texto.split("\n") if l.strip()]


def arquivos_revertidos(root, feeder, snapshot):
    """Lista (status, path) do que o snapshot mudou (vs o pai dele) e o feeder
    DESFEZ — isto é, no feeder o blob é o do avô, não o do snapshot. O que o
    feeder mudou por conta própria (pinnacle/superbet: ponteiros, _snapshots,
    _status) não é reversão e fica de fora."""
    avo = _git(root, "rev-parse", f"{snapshot}^").strip()
    out = []
    for ln in _git(root, "diff-tree", "-r", "--name-status", avo, snapshot).splitlines():
        if not ln.strip():
            continue
        st, path = ln.split("\t", 1)
        b_feeder, b_avo, b_snap = _blob(root, feeder, path), _blob(root, avo, path), _blob(root, snapshot, path)
        if b_feeder == b_avo and b_feeder != b_snap:
            out.append((st[0], path))
    return avo, out


def classificar(path):
    p = path
    if p.startswith("data/odds_history/ticks/") and p.endswith(".jsonl"):
        return "ticks"
    if p.startswith("data/odds/_snapshots/"):
        return "snapshot_odds"
    if p.startswith("data/fixtures/_snapshots/"):
        return "snapshot_fixtures"
    if (p.startswith("data/odds/") and "_latest" in p and p.count("/") == 2) or \
       (p.startswith("data/fixtures/sofa_latest") and p.endswith(".json")):
        return "ponteiro"
    if p.startswith("data/odds_history/keys/"):
        return "derivado:keys"
    if p.startswith("data/odds_history/ledger/"):
        return "derivado:ledger"
    if p.startswith("data/odds_history/clv/"):
        return "derivado:clv"
    if p.startswith("data/odds_history/results/"):
        return "derivado:results"
    if p.startswith("data/odds/_status/"):
        return "derivado:_status"
    if p.startswith("valor/data/"):
        return "derivado:valor/data"
    return "derivado:outros"


# ─────────────────────────────── núcleo ─────────────────────────────────────

def recuperar(root, pares, apply=False, so_ticks=False, log=print):
    """Roda os pares em ordem (o 2º par mescla em cima do resultado do 1º —
    dois deles tocam o mesmo dia). Devolve o relatório em dict; grava só com
    apply=True, e só na working tree (nenhum comando de commit aqui)."""
    root = Path(root)
    _git(root, "rev-parse", "--is-inside-work-tree")
    estado_ticks = {}          # path -> linhas (visão da working tree, atualizada par a par)
    tocados_ticks = set()
    rel = {"pares": [], "ticks_por_dia": Counter(), "ticks_stats": Counter(),
           "snapshots_restaurar": [], "snapshots_ja_existem": [],
           "snapshots_apagados_pelo_snapshot": [],
           "ponteiros_mantidos": [], "ponteiros_restaurar": [],
           "derivados": Counter(), "derivados_linhas": Counter(), "apply": bool(apply)}

    for feeder, snapshot in pares:
        f_full = _git(root, "rev-parse", "--verify", f"{feeder}^{{commit}}").strip()
        s_full = _git(root, "rev-parse", "--verify", f"{snapshot}^{{commit}}").strip()
        avo, revertidos = arquivos_revertidos(root, f_full, s_full)
        numstat = {}
        for ln in _git(root, "diff-tree", "-r", "--numstat", f_full, s_full).splitlines():
            parts = ln.split("\t")
            if len(parts) == 3:
                numstat[parts[2]] = (parts[0], parts[1])
        info = {"feeder": f_full[:9], "snapshot": s_full[:9], "avo": avo[:9],
                "snapshot_msg": _git(root, "log", "-1", "--format=%ci %s", s_full).strip(),
                "feeder_msg": _git(root, "log", "-1", "--format=%ci %s", f_full).strip(),
                "n_revertidos": len(revertidos), "por_classe": Counter(), "ticks": []}
        log(f"\n=== feeder {info['feeder']} desfez snapshot {info['snapshot']} (avô {info['avo']}) ===")
        log(f"    snapshot: {info['snapshot_msg']}")
        log(f"    feeder  : {info['feeder_msg']}")
        log(f"    arquivos revertidos: {len(revertidos)}")

        for st, path in revertidos:
            classe = classificar(path)
            info["por_classe"][classe] += 1

            if classe == "ticks":
                if st == "D":
                    continue  # snapshot apagou um ticks? não acontece; nunca apagamos nada
                if path not in estado_ticks:
                    wt = root / path
                    estado_ticks[path] = _linhas(wt.read_text(encoding="utf-8")) if wt.exists() else []
                extra = _linhas(_conteudo(root, s_full, path))
                antes = len(estado_ticks[path])
                merged, st_m = mesclar_ticks(estado_ticks[path], extra)
                estado_ticks[path] = merged
                if st_m.get("novas", 0) or st_m.get("novas_json_invalido", 0):
                    tocados_ticks.add(path)
                dia = Path(path).stem
                rel["ticks_por_dia"][dia] += st_m.get("novas", 0)
                rel["ticks_stats"].update(st_m)
                info["ticks"].append({"path": path, "snapshot_linhas": len(extra), "wt_antes": antes,
                                      "wt_depois": len(merged), **st_m})
                log(f"    [ticks] {dia}: snapshot={len(extra)} · wt {antes}→{len(merged)} · "
                    f"NOVAS={st_m.get('novas', 0)} (open {st_m.get('novas:open', 0)}, price {st_m.get('novas:price', 0)}, "
                    f"line_open {st_m.get('novas:line_open', 0)}, line_move {st_m.get('novas:line_move', 0)}) · "
                    f"já no main: exato {st_m.get('ja_no_main_exato', 0)}, lógico {st_m.get('ja_no_main_logico', 0)}, "
                    f"identidade divergente {st_m.get('ja_no_main_identidade_divergente', 0)}")
                continue

            if classe in ("snapshot_odds", "snapshot_fixtures"):
                if so_ticks:
                    continue
                if st == "D":
                    # o snapshot PODOU esta geração (current+previous); o feeder a
                    # trouxe de volta e o main já podou de novo. Nada a fazer.
                    rel["snapshots_apagados_pelo_snapshot"].append(path)
                    continue
                wt = root / path
                if wt.exists():
                    rel["snapshots_ja_existem"].append(path)
                    log(f"    [snapshot] já existe na working tree: {path}")
                    continue
                dados = _conteudo(root, s_full, path, binario=True)
                rel["snapshots_restaurar"].append((path, len(dados)))
                log(f"    [snapshot] restaurar {path} ({len(dados):,} bytes)")
                if apply:
                    wt.parent.mkdir(parents=True, exist_ok=True)
                    wt.write_bytes(dados)
                continue

            if classe == "ponteiro":
                if so_ticks:
                    continue
                wt = root / path
                snap_txt = _conteudo(root, s_full, path)
                if wt.exists() and ponteiro_mais_novo_ou_igual(wt.read_text(encoding="utf-8"), snap_txt):
                    try:
                        at_wt = json.loads(wt.read_text(encoding="utf-8")).get("at")
                    except ValueError:
                        at_wt = "?"
                    at_sn = json.loads(snap_txt).get("at")
                    rel["ponteiros_mantidos"].append((path, at_wt, at_sn))
                    log(f"    [ponteiro] mantido (wt at={at_wt} ≥ snapshot at={at_sn}): {path}")
                    continue
                rel["ponteiros_restaurar"].append(path)
                log(f"    [ponteiro] RESTAURAR (wt ausente ou mais velho): {path}")
                if apply:
                    atomic_write_text(wt, snap_txt)
                continue

            # derivado: só relata
            add, dele = numstat.get(path, ("?", "?"))
            rel["derivados"][classe] += 1
            try:
                rel["derivados_linhas"][classe] += int(add)
            except ValueError:
                pass
            log(f"    [{classe}] NÃO restaurado (derivado) {st} {path}  linhas +{add}/-{dele}")

        info["por_classe"] = dict(info["por_classe"])
        rel["pares"].append(info)

    if apply:
        for path in sorted(tocados_ticks):
            atomic_write_text(root / path, "\n".join(estado_ticks[path]) + "\n")

    # ── resumo ──
    log("\n=== RESUMO ===")
    tot = rel["ticks_stats"]
    log(f"ticks: NOVAS={tot.get('novas', 0)} linhas "
        f"(open {tot.get('novas:open', 0)} · price {tot.get('novas:price', 0)} · "
        f"line_open {tot.get('novas:line_open', 0)} · line_move {tot.get('novas:line_move', 0)}) · "
        f"já no main: exato {tot.get('ja_no_main_exato', 0)} / lógico {tot.get('ja_no_main_logico', 0)} / "
        f"identidade divergente {tot.get('ja_no_main_identidade_divergente', 0)}")
    for dia, n in sorted(rel["ticks_por_dia"].items()):
        log(f"    {dia}: +{n} linhas")
    log(f"snapshots crus a restaurar: {len(rel['snapshots_restaurar'])} "
        f"({sum(b for _, b in rel['snapshots_restaurar']):,} bytes) · já existem: {len(rel['snapshots_ja_existem'])} · "
        f"gerações podadas pelo próprio snapshot (ignoradas): {len(rel['snapshots_apagados_pelo_snapshot'])}")
    log(f"ponteiros: mantidos {len(rel['ponteiros_mantidos'])} · a restaurar {len(rel['ponteiros_restaurar'])}")
    log("derivados NÃO restaurados: " + ", ".join(f"{k} {v} arq (+{rel['derivados_linhas'][k]} linhas)"
                                                 for k, v in sorted(rel["derivados"].items())))
    log(("APLICADO na working tree (sem commit): " if apply else "DRY-RUN — nada gravado. Tocaria: ")
        + f"{len(tocados_ticks)} ticks, {len(rel['snapshots_restaurar'])} snapshots, {len(rel['ponteiros_restaurar'])} ponteiros")
    rel["ticks_tocados"] = sorted(tocados_ticks)
    return rel


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--par", action="append", metavar="FEEDER:SNAPSHOT",
                    help="par (feeder_sha:snapshot_sha); repetível. Padrão: os 4 de 03-04/09/2026")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="só imprime (padrão)")
    g.add_argument("--apply", action="store_true", help="grava na working tree (NUNCA commita)")
    ap.add_argument("--so-ticks", action="store_true", help="só mescla ticks (ignora _snapshots e ponteiros)")
    ap.add_argument("--root", default=str(ROOT), help="raiz do repositório (padrão: pasta deste script)")
    a = ap.parse_args(argv)
    pares = []
    for p in (a.par or []):
        if ":" not in p:
            ap.error(f"--par precisa ser FEEDER:SNAPSHOT, veio {p!r}")
        f, s = p.split(":", 1)
        pares.append((f.strip(), s.strip()))
    pares = pares or PARES_0509
    recuperar(a.root, pares, apply=a.apply, so_ticks=a.so_ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
