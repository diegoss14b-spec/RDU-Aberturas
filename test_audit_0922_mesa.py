# -*- coding: utf-8 -*-
"""Regressões da auditoria de 22/09/2026 na Mesa (A01b, A01c, A13a-d). Offline, sem rede.

A01b  model_state por sinal: selo+penalidade; bloqueio só com "atrás do RDU" comprovado
      (manifesto lido e >6 h de atraso contadas pela Mesa); ilegível nunca bloqueia; o
      juiz do mesa_bot respeita o MESMO bloqueio (paridade).
A13a  Pinnacle pelo FEED EFETIVO: tentativa do Actions com 429 e full local fresco =
      protected_feed; fronteira 74/76 min; skip da tentativa só em FULL.
A13b  janela de 24 h (com n mínimo) dispara o aviso; motivo curto no histórico.
A13c  backlog por classe: universo RDU sem resultado segue no alarme mesmo em dia coberto.
A13d  deploy.py emite ::error:: com o motivo real.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import model_freshness as mf
from mesa_shared import MODEL_BEHIND_BLOCK_H, MODEL_MAX_AGE_DAYS

UTC = timezone.utc
BRT = timezone(timedelta(hours=-3))
NOW = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)
BUNDLE = {"version": "2026-08-29", "generated_at": "2026-09-08T02:32:00-03:00", "data_sha256": "aaa"}
MAN_NOVO = {"candidates_version": "2026-09-21", "data_sha256": "bbb", "generated_at": "2026-09-22T04:27:42Z"}


class ModelFreshnessTests(unittest.TestCase):
    def test_parse_manifest_rejects_login_page(self):
        self.assertIsNone(mf.parse_manifest('<html><script>var x={"candidates_version":"x"}</script>'))
        self.assertIsNone(mf.parse_manifest("window.MODELS_MANIFEST={};"))
        self.assertEqual(mf.parse_manifest('window.MODELS_MANIFEST={"candidates_version":"2026-09-21"};')
                         ["candidates_version"], "2026-09-21")

    def test_current_when_same_version(self):
        fr = mf.assess(dict(BUNDLE, version="2026-09-21", data_sha256="bbb"), MAN_NOVO, NOW, env={})
        self.assertEqual((fr["state"], fr["block"]), ("current", False))
        self.assertEqual(mf.signal_state(fr, 1), ("current", "ok"))

    def test_behind_blocks_only_after_6h_counted_by_the_mesa(self):
        fr = mf.assess(BUNDLE, MAN_NOVO, NOW, prev={}, env={})
        self.assertEqual((fr["state"], fr["block"]), ("behind_rdu", False))   # 1ª vez: relógio começa agora
        self.assertEqual(mf.signal_state(fr, 1), ("behind_rdu", "warn"))
        prev = fr
        later = NOW + timedelta(hours=MODEL_BEHIND_BLOCK_H - 0.1)
        self.assertFalse(mf.assess(BUNDLE, MAN_NOVO, later, prev=prev, env={})["block"])
        later = NOW + timedelta(hours=MODEL_BEHIND_BLOCK_H + 0.1)
        fr2 = mf.assess(BUNDLE, MAN_NOVO, later, prev=prev, env={})
        self.assertTrue(fr2["block"])
        self.assertEqual(mf.signal_state(fr2, 1), ("behind_rdu", "block"))

    def test_unreadable_manifest_never_blocks_and_keeps_the_clock(self):
        prev = mf.assess(BUNDLE, MAN_NOVO, NOW, prev={}, env={})
        fr = mf.assess(BUNDLE, None, NOW + timedelta(hours=10), prev=prev, error="URLError", env={})
        self.assertEqual((fr["state"], fr["block"], fr["reason"]), ("unverified", False, "URLError"))
        self.assertEqual(fr["behind"], prev["behind"])        # rede caiu: o atraso não zera
        back = mf.assess(BUNDLE, MAN_NOVO, NOW + timedelta(hours=11), prev=fr, env={})
        self.assertTrue(back["block"])                         # voltou a ler: 11 h comprovadas
        self.assertEqual(mf.signal_state(fr, 3), ("unverified", "warn"))

    def test_new_version_pair_restarts_the_clock(self):
        prev = mf.assess(BUNDLE, MAN_NOVO, NOW, prev={}, env={})
        man3 = dict(MAN_NOVO, candidates_version="2026-09-28")
        fr = mf.assess(BUNDLE, man3, NOW + timedelta(hours=10), prev=prev, env={})
        self.assertFalse(fr["block"])

    def test_escape_hatches_and_ahead(self):
        prev = mf.assess(BUNDLE, MAN_NOVO, NOW - timedelta(hours=10), prev={}, env={})
        self.assertFalse(mf.assess(BUNDLE, MAN_NOVO, NOW, prev=prev, env={"MODEL_FROZEN_OK": "2026-08-29"})["block"])
        self.assertFalse(mf.assess(BUNDLE, MAN_NOVO, NOW, prev=prev, env={"MODEL_GATE": "off"})["block"])
        ahead = mf.assess(dict(BUNDLE, version="2026-10-01"), MAN_NOVO, NOW, env={})
        self.assertEqual((ahead["state"], ahead["block"]), ("unverified", False))

    def test_stale_age_is_warn_never_block(self):
        fr = mf.assess(dict(BUNDLE, version="2026-09-21", data_sha256="bbb"), MAN_NOVO, NOW, env={})
        self.assertEqual(mf.signal_state(fr, MODEL_MAX_AGE_DAYS + 1), ("stale_age", "warn"))
        self.assertEqual(mf.signal_state(fr, MODEL_MAX_AGE_DAYS), ("current", "ok"))
        self.assertEqual(mf.signal_state(None, 1), ("unverified", "warn"))

    def test_state_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            fr = mf.assess(BUNDLE, MAN_NOVO, NOW, prev={}, env={})
            mf.save_state(td, fr)
            self.assertEqual(mf.load_state(td)["behind"], fr["behind"])
            self.assertEqual(mf.load_state(td + "/nao_existe"), {})

    def test_judge_respects_proven_block(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent / "mesa_bot"))
        from judge import judge_board
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(judge_board({"model": {"freshness": {"block": True, "bundle_version": "a",
                                                                   "rdu_version": "b", "hours_behind": 7}},
                                          "jogos": [{"jogo": "x"}]}), [])
        self.assertIn("bloqueio comprovado", out.getvalue())


class CaptureHealthLocalFeedTests(unittest.TestCase):
    def pointer(self, td, age_min, captured_by="local", n=55):
        d = Path(td)
        (d / "_snapshots").mkdir(exist_ok=True)
        (d / "_snapshots/pinnacle_full_x.jsonl").write_text("{}\n")
        at = (NOW - timedelta(minutes=age_min)).astimezone(BRT).isoformat(timespec="seconds")
        (d / "pinnacle_latest_full.json").write_text(json.dumps(
            {"file": "_snapshots/pinnacle_full_x.jsonl", "n": n, "at": at, "captured_by": captured_by}))

    def test_boundary_74_protected_76_failed(self):
        from capture_health import state, local_feed_age_min
        falhou = {"ok": False, "ts_utc": NOW.isoformat(), "error": "HTTP429", "error_class": "Other"}
        with tempfile.TemporaryDirectory() as td:
            self.pointer(td, 74)
            self.assertEqual(state(falhou, "pinnacle", NOW, local_feed_min=local_feed_age_min("pinnacle", td, NOW)),
                             "protected_feed")
            self.pointer(td, 76)
            self.assertEqual(state(falhou, "pinnacle", NOW, local_feed_min=local_feed_age_min("pinnacle", td, NOW)),
                             "failed")
            self.pointer(td, 10, captured_by="actions")      # full da NUVEM não é feed local
            self.assertIsNone(local_feed_age_min("pinnacle", td, NOW))
            self.pointer(td, 10, n=0)
            self.assertIsNone(local_feed_age_min("pinnacle", td, NOW))
        # sem ponteiro: comportamento antigo
        self.assertEqual(state(falhou, "pinnacle", NOW), "failed")
        # sucesso da tentativa continua 'ok'; feed local não vira 'ok' sozinho
        self.assertEqual(state(dict(falhou, ok=True), "pinnacle", NOW, local_feed_min=5), "ok")

    def test_skipped_attempt_is_protected_not_ok(self):
        from capture_health import state
        feeder_ok = {"ok": True, "ts_utc": NOW.isoformat(), "skipped_reason": "local_feed_fresh", "attempted": False}
        self.assertEqual(state(feeder_ok, "pinnacle", NOW), "protected_feed")
        self.assertEqual(state(feeder_ok, "pinnacle", NOW, local_feed_min=80), "failed")

    def test_board_capture_uses_effective_feed(self):
        from capture_health import board_capture, ACTIVE_HOUSES
        with tempfile.TemporaryDirectory() as td:
            status = Path(td) / "_status"
            status.mkdir()
            for h in ACTIVE_HOUSES:
                (status / (h + ".json")).write_text(json.dumps({"ok": True, "ts_utc": NOW.isoformat()}))
            (status / "pinnacle.json").write_text(json.dumps({"ok": False, "ts_utc": NOW.isoformat(),
                                                              "error": "HTTP429"}))
            self.pointer(td, 30)
            cap = board_capture(status, [], NOW)
            self.assertIn("Pinnacle", cap["casas_protected"])
            self.assertNotIn("Pinnacle", [f["casa"] for f in cap["casas_fail"]])

    def test_run_capture_skip_only_full_and_inside_guard(self):
        import run_capture as rc
        with tempfile.TemporaryDirectory() as td:
            self.pointer(td, 74)
            self.assertIsNotNone(rc.local_feed_skip_min("pinnacle", True, odds_dir=td, env={}, now=NOW))
            self.assertIsNone(rc.local_feed_skip_min("pinnacle", False, odds_dir=td, env={}, now=NOW))   # close tenta
            self.assertIsNone(rc.local_feed_skip_min("betano", True, odds_dir=td, env={}, now=NOW))
            self.assertIsNone(rc.local_feed_skip_min("pinnacle", True, odds_dir=td,
                                                     env={"LOCAL_FEED_SKIP": "0"}, now=NOW))
            self.pointer(td, 76)
            self.assertIsNone(rc.local_feed_skip_min("pinnacle", True, odds_dir=td, env={}, now=NOW))


class OpsTests(unittest.TestCase):
    def test_24h_window_drives_the_warning(self):
        import build_ops as bo
        h7 = {"bet365": {"ok": 71, "total": 151, "rate": 47.0}}
        self.assertEqual(bo.confiabilidade_avisos({"bet365": {"ok": 26, "total": 26, "rate": 100.0}}, h7), [])
        av = bo.confiabilidade_avisos({"bet365": {"ok": 1, "total": 4, "rate": 25.0,
                                                  "motivos": {"captura parcial: rede/orçamento": 3}}}, h7)
        self.assertEqual(len(av), 1)
        self.assertIn("24h em 25.0%", av[0]["txt"])
        self.assertIn("rede/orçamento", av[0]["txt"])
        self.assertEqual(bo.confiabilidade_avisos({"bet365": {"ok": 0, "total": 2, "rate": 0.0}}, h7), [])  # n mínimo

    def test_hist_agg_counts_local_feed_and_motives(self):
        import build_ops as bo
        rows = [{"casas": {"pinnacle": {"attempted": False, "skipped_reason": "local_feed_fresh"},
                           "bet365": {"ok": False, "source_state": "failed", "error": "captura parcial: rede/orçamento"}}},
                {"casas": {"bet365": {"ok": True, "source_state": "ok", "n": 99}}}]
        agg = bo._hist_agg(rows)
        self.assertEqual(agg["Pinnacle"]["local_feed"], 1)
        self.assertEqual(agg["Pinnacle"]["total"], 0)
        self.assertEqual((agg["bet365"]["ok"], agg["bet365"]["total"]), (1, 2))
        self.assertEqual(agg["bet365"]["motivos"], {"captura parcial: rede/orçamento": 1})

    def test_model_and_ref_feed_warnings(self):
        import build_ops as bo
        board = {"model": {"freshness": {"state": "behind_rdu", "block": True, "bundle_version": "2026-08-29",
                                         "rdu_version": "2026-09-21", "hours_behind": 7.2},
                           "ref_feed": {"stale": True, "age_hours": 60, "max_age_hours": 48}}}
        av = bo.model_avisos(board)
        self.assertEqual([a["level"] for a in av], ["bad", "warn"])
        self.assertIn("FORA de Acionáveis", av[0]["txt"])
        self.assertEqual(bo.model_avisos({"model": {"freshness": {"state": "current"}}}), [])
        self.assertEqual(bo.model_avisos({"model": {"freshness": {"state": "unverified", "reason": "URLError"}}})[0]["level"], "info")

    def test_backlog_alarm_by_class(self):
        import build_ops as bo
        classes = {"fora_do_universo": {"7-30d": 25000}, "universo_sem_resultado": {"7-30d": 12},
                   "stat_missing_cronico": {"30d+": 5}, "cartoes_sem_divisao_vermelho": {"7-30d": 7}}
        av = bo.backlog_avisos({"classes": classes, "total": 25024, "age": {"7-30d": 25019, "30d+": 5}})
        alarme = [a for a in av if a["level"] == "warn"][0]["txt"]
        self.assertIn("Liquidação atrasada: 19 keys", alarme)          # só universo + R2
        self.assertIn("universo RDU", alarme)
        info = [a for a in av if a["level"] == "info"][0]["txt"]
        self.assertIn("25000 fora do universo", info)
        # status antigo sem classes: comportamento de antes
        self.assertIn("Liquidação atrasada: 25024", bo.backlog_avisos({"age": {"7-30d": 25019, "30d+": 5}})[0]["txt"])


class SettleBacklogClassTests(unittest.TestCase):
    def test_classes(self):
        from history_settle import backlog_class, STAT_MISSING_TETO_DIAS
        cob = {"2026-09-10": 40}
        base = {"status": "pending_result", "settlement_reason": "game_not_in_results",
                "kickoff": "2026-09-10T16:00:00-03:00"}
        self.assertEqual(backlog_class(base, 12, cob), "fora_do_universo")
        self.assertEqual(backlog_class(dict(base, sofa_id=123), 12, cob), "universo_sem_resultado")   # cético 22/09
        self.assertEqual(backlog_class(dict(base, kickoff="2026-09-12T16:00:00-03:00"), 10, {}), "feed_sem_o_dia")
        sm = dict(base, settlement_reason="stat_missing:corners")
        self.assertEqual(backlog_class(sm, STAT_MISSING_TETO_DIAS, cob), "stat_missing")
        self.assertEqual(backlog_class(sm, STAT_MISSING_TETO_DIAS + 1, cob), "stat_missing_cronico")
        self.assertEqual(backlog_class(dict(base, status="pending_semantics"), 9, cob), "cartoes_sem_divisao_vermelho")

    def test_status_carries_classes(self):
        from history_settle import build_settlement_status
        now = datetime(2026, 9, 22, 19, 0, tzinfo=BRT)
        rec = {"status": "pending_result", "settlement_reason": "game_not_in_results",
               "kickoff": "2026-09-10T16:00:00-03:00", "sofa_id": 1}
        s = build_settlement_status([("betano|sofa:1|Cartões|4.5|over", rec)], [], now)
        self.assertEqual(s["backlog"]["classes"], {"universo_sem_resultado": {"7-30d": 1}})


class DeployAnnotationTests(unittest.TestCase):
    def test_failure_reason_becomes_annotation(self):
        import deploy
        with mock.patch.object(deploy, "TOKEN", ""), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(deploy.main(), 1)
        self.assertIn("::error title=deploy::sem NETLIFY_TOKEN", out.getvalue())

    def test_api_exception_is_annotated(self):
        import urllib.error
        import deploy

        def boom():
            raise urllib.error.HTTPError("https://api.netlify.com/x", 422, "x", {}, None)
        with mock.patch.object(deploy, "main", boom), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(deploy.run(), 1)
        self.assertIn("::error title=deploy::API do Netlify HTTP 422", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
