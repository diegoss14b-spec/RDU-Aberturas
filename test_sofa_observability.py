# -*- coding: utf-8 -*-
"""D11 (30/09/2026): observabilidade da captura Sofascore da Mesa.

Tudo offline: `_transport_get` é sempre mock e o socket fica bloqueado durante
cada teste (qualquer tentativa de rede reprova o teste em vez de sair da máquina).
O que se trava aqui é só o REGISTRO da falha — rota, headers, impersonate,
tentativas, disjuntor e promoção continuam como estavam (e os testes abaixo
conferem que continuam).
"""
import contextlib
import hashlib
import io
import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import capture_common as cc
import fetch_fixtures_sofascore as ff

SECRET_USER = "SEGREDOUSUARIO"
SECRET_PASS = "SEGREDOSENHA"
PROX = {
    "http": f"http://user-{SECRET_USER}-country-br:{SECRET_PASS}@gate.decodo.invalid:7000",
    "https": f"http://user-{SECRET_USER}-country-br:{SECRET_PASS}@gate.decodo.invalid:7000",
}
CHALLENGE = b'{"error": {"code": 403, "reason": "challenge"}}\n'
SEASONS_URL = "https://api.sofascore.com/api/v1/unique-tournament/325/seasons"


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "replace")
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class ProxyError(ConnectionError):
    """Imita o ProxyError dos clientes: a mensagem crua traz a URL do proxy."""


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        # Toda tentativa de rede é ANOTADA e reprova o teste no fim, mesmo quando o
        # AssertionError é engolido pelo `except Exception` do get() (que trataria a
        # trava como uma exceção de transporte qualquer).
        self.net_attempts = []
        self.addCleanup(self._assert_no_network_attempt)

        def _no_network(*args, **kwargs):
            self.net_attempts.append(repr(args)[:120])
            raise AssertionError("teste tentou abrir rede: %r" % (args,))

        # socket do Python cobre o fallback `requests`; o cliente real do CI é o
        # curl_cffi (libcurl, socket em C, passa por fora do módulo socket) — por
        # isso a trava também fica NO CLIENTE. Teste que precisa do cliente põe o
        # próprio patch por cima (test_request_contract_is_the_same).
        for target, attr in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                             (socket, "getaddrinfo"), (socket, "create_connection"),
                             (ff._HTTP_CLIENT, "get")):
            p = patch.object(target, attr, _no_network)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(ff.time, "sleep", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        ff._reset_get_diag()
        self.addCleanup(ff._reset_get_diag)

    def _assert_no_network_attempt(self):
        if self.net_attempts:
            raise AssertionError("teste tentou abrir rede: %s" % self.net_attempts)


class NetworkGuardTest(OfflineTestCase):
    # alvo local e fechado: mesmo sem a trava (controle negativo), nada sai da máquina
    LOCAL_CLOSED = "http://127.0.0.1:9/trava-de-rede"

    def test_guard_covers_the_real_http_client(self):
        with self.assertRaises(AssertionError):
            ff._HTTP_CLIENT.get(self.LOCAL_CLOSED, timeout=1)
        with self.assertRaises(AssertionError):
            ff._transport_get(self.LOCAL_CLOSED, None)
        self.assertEqual(2, len(self.net_attempts))
        self.net_attempts.clear()   # a tentativa aqui é a prova, não um vazamento

    def test_guard_is_loud_even_when_get_swallows_the_error(self):
        # get() sem mock de _transport_get: o `except Exception` engole a trava, mas a
        # tentativa fica anotada (e o cleanup reprovaria qualquer outro teste assim).
        with patch.multiple(ff, PROX=None):
            self.assertIsNone(ff.get(self.LOCAL_CLOSED, tries=1))
        self.assertEqual(1, len(self.net_attempts))
        self.assertEqual("AssertionError", ff._GET_DIAG["last_exception"]["type"])
        self.net_attempts.clear()


class RefusalRecordTest(OfflineTestCase):
    def test_challenge_403_is_recorded_without_extra_attempt(self):
        resp = FakeResponse(403, CHALLENGE, {"Content-Type": "application/json", "Server": "Varnish"})
        with patch.multiple(ff, PROX=PROX):
            with patch.object(ff, "_transport_get", return_value=resp) as request:
                self.assertIsNone(ff.get(SEASONS_URL + "?token=abc#frag", tries=2))
                # disjuntor aberto: a próxima liga não toca a rede
                self.assertIsNone(ff.get(SEASONS_URL, tries=2))

        request.assert_called_once_with(SEASONS_URL + "?token=abc#frag", PROX)
        d = ff._diag_snapshot()
        self.assertEqual(1, d["requests"])
        self.assertEqual(1, d["proxy_attempts"])
        self.assertEqual(0, d["direct_attempts"])
        self.assertTrue(d["circuit_open"])
        self.assertEqual("recusa_destino", d["circuit_cause"])
        self.assertEqual({
            "status": 403, "rota": "proxy", "classe": "recusa_destino",
            "endpoint": "api.sofascore.com/api/v1/unique-tournament/325/seasons",
            "content_type": "application/json", "server": "Varnish",
            "body_len": len(CHALLENGE),
            "sha256_16": hashlib.sha256(CHALLENGE).hexdigest()[:16],
            "reason": "challenge",
        }, {k: v for k, v in d["last_refusal"].items() if k != "ts_utc"})
        datetime.strptime(d["last_refusal"]["ts_utc"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertIn("recusa=403:challenge@proxy", ff._diag_text())
        self.assertIn("causa=recusa_destino", ff._diag_text())

    def test_direct_route_label_is_direto(self):
        resp = FakeResponse(403, CHALLENGE, {"content-type": "application/json"})
        with patch.multiple(ff, PROX=None):
            with patch.object(ff, "_transport_get", return_value=resp) as request:
                ff.get(SEASONS_URL, tries=2)
        request.assert_called_once_with(SEASONS_URL, None)
        self.assertEqual("direto", ff._GET_DIAG["last_refusal"]["rota"])
        self.assertEqual(1, ff._GET_DIAG["direct_attempts"])

    def test_reason_is_sanitized_and_bounded(self):
        body = json.dumps({"error": {"code": 403, "reason":
                                     "Chal<script>lenge!!\n http://x:y@z " + "a" * 80}}).encode()
        with patch.object(ff, "_transport_get", return_value=FakeResponse(403, body)):
            ff.get(SEASONS_URL, tries=1)
        reason = ff._GET_DIAG["last_refusal"]["reason"]
        self.assertLessEqual(len(reason), 40)
        self.assertRegex(reason, r"^[a-z0-9_ -]+$")
        self.assertTrue(reason.startswith("chalscriptlenge"), reason)
        self.assertNotIn(":", reason)
        self.assertNotIn("@", reason)

    def test_403_without_readable_reason_is_not_geo(self):
        for body in (b"blocked", b"<html>denied</html>", b'{"error": "texto"}',
                     b'{"error": {"reason": 42}}', b"{" * 10, b"{" + b" " * (ff._BODY_JSON_MAX + 1) + b"}"):
            ff._reset_get_diag()
            with patch.object(ff, "_transport_get", return_value=FakeResponse(403, body)) as request:
                self.assertIsNone(ff.get(SEASONS_URL, tries=2))
            self.assertEqual(1, request.call_count)
            rec = ff._GET_DIAG["last_refusal"]
            self.assertIsNone(rec["reason"], body[:30])
            self.assertEqual("recusa_http", rec["classe"])
            self.assertEqual(len(body), rec["body_len"])
            self.assertEqual("recusa_http", ff._sofa_error_class("snapshot não saudável; HTTP 403 via proxy"))

    def test_response_without_headers_or_content_does_not_break_the_403_path(self):
        class Bare:
            status_code = 403
            text = "blocked"
            headers = None

        with patch.object(ff, "_transport_get", return_value=Bare()) as request:
            self.assertIsNone(ff.get(SEASONS_URL, tries=2))
        self.assertEqual(1, request.call_count)
        rec = ff._GET_DIAG["last_refusal"]
        self.assertIsNone(rec["content_type"])
        self.assertEqual(len(b"blocked"), rec["body_len"])
        self.assertTrue(ff._GET_DIAG["circuit_open"])

    def test_recorder_failure_never_turns_403_into_a_retry(self):
        with patch.object(ff, "_body_bytes", side_effect=RuntimeError("quebra")):
            with patch.object(ff, "_transport_get", return_value=FakeResponse(403, CHALLENGE)) as request:
                self.assertIsNone(ff.get(SEASONS_URL, tries=2))
        self.assertEqual(1, request.call_count)
        self.assertTrue(ff._GET_DIAG["circuit_open"])
        self.assertEqual({"status": 403, "rota": "proxy" if ff.PROX else "direto",
                          "classe": "recusa_http"}, ff._GET_DIAG["last_refusal"])

    def test_other_non_200_are_recorded_and_keep_their_flow(self):
        cases = [
            (429, b'{"error": {"code": 429, "reason": "rate"}}', "limite_429", 2, False),
            (503, b"<html>upstream</html>", "transporte", 2, False),
            (200, b"<html>not json</html>", "schema", 2, False),
            (404, b'{"error": {"code": 404, "reason": "Not Found"}}', "nao_encontrado", 1, False),
            (407, b"", "proxy_erro", 1, True),
        ]
        for status, body, classe, calls, circuit in cases:
            ff._reset_get_diag()
            resp = FakeResponse(status, body, {"content-type": "text/html"})
            with patch.object(ff, "_transport_get", return_value=resp) as request:
                self.assertIsNone(ff.get(SEASONS_URL, tries=2))
            self.assertEqual(calls, request.call_count, status)
            rec = ff._GET_DIAG["last_refusal"]
            self.assertEqual((status, classe), (rec["status"], rec["classe"]))
            self.assertEqual("text/html", rec["content_type"])
            self.assertEqual(circuit, ff._GET_DIAG["circuit_open"], status)

    def test_success_is_unchanged_and_clears_attempt_class(self):
        payload = {"seasons": [{"id": 1}]}
        with patch.object(ff, "_transport_get",
                          return_value=FakeResponse(200, json.dumps(payload).encode())):
            self.assertEqual(payload, ff.get(SEASONS_URL, tries=2))
        self.assertIsNone(ff._GET_DIAG["last_attempt_class"])
        self.assertIsNone(ff._GET_DIAG["last_refusal"])
        self.assertIsNone(ff._GET_DIAG["last_error"])


class ExceptionRecordTest(OfflineTestCase):
    def _secret_free(self, obj):
        blob = json.dumps(obj, ensure_ascii=False)
        self.assertNotIn(SECRET_PASS, blob)
        self.assertNotIn(SECRET_USER, blob)
        self.assertNotIn("decodo", blob.lower())

    def test_proxy_exception_keeps_only_type_name_and_class(self):
        exc = ProxyError(f"Unable to connect to proxy {PROX['https']} (CONNECT 407)")
        with patch.multiple(ff, PROX=PROX):
            with patch.object(ff, "_transport_get", side_effect=exc) as request:
                self.assertIsNone(ff.get(SEASONS_URL, tries=2))
        # exceção de transporte segue com as MESMAS 2 tentativas de antes
        self.assertEqual(2, request.call_count)
        self.assertEqual({"type": "ProxyError", "classe": "proxy_erro", "rota": "proxy"},
                         ff._GET_DIAG["last_exception"])
        self.assertEqual("ProxyError via proxy", ff._GET_DIAG["last_error"])
        self._secret_free(ff._diag_snapshot())
        self.assertNotIn(SECRET_PASS, ff._diag_text())

    def test_real_client_exception_classes(self):
        classes = []
        try:
            from curl_cffi.requests import exceptions as ce
            classes += [(ce.ProxyError, "proxy_erro"), (ce.ConnectTimeout, "timeout"),
                        (ce.ReadTimeout, "timeout"), (ce.SSLError, "tls"),
                        (ce.ConnectionError, "transporte")]
        except ImportError:
            pass
        try:
            import requests.exceptions as re_
            classes += [(re_.ProxyError, "proxy_erro"), (re_.ConnectTimeout, "timeout"),
                        (re_.ConnectionError, "transporte")]
        except ImportError:
            pass
        classes += [(TimeoutError, "timeout"), (json.JSONDecodeError, "schema"), (OSError, "transporte")]
        for cls, expected in classes:
            try:
                exc = cls("msg", "doc", 0) if cls is json.JSONDecodeError else cls("msg")
            except TypeError:
                exc = cls()
            self.assertEqual(expected, ff._exception_class(exc), cls)

    def test_invalid_json_on_200_is_schema(self):
        with patch.object(ff, "_transport_get", return_value=FakeResponse(200, b"{not json")):
            self.assertIsNone(ff.get(SEASONS_URL, tries=1))
        self.assertEqual("schema", ff._GET_DIAG["last_exception"]["classe"])


class RunLevelTest(OfflineTestCase):
    def _run_main(self, tournaments, transport, prox=PROX):
        td = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        out = root / "data" / "fixtures"
        status = root / "data" / "odds" / "_status"
        out.mkdir(parents=True)
        at = datetime.now(timezone.utc).astimezone(ff.BRT).isoformat(timespec="seconds")
        (out / "prev.json").write_text(json.dumps({"fixtures": [{"sofa_id": i} for i in range(40)]}),
                                       encoding="utf-8")
        pointer = {"file": "prev.json", "n": 40, "at": at}
        (out / "sofa_latest.json").write_text(json.dumps(pointer), encoding="utf-8")
        log = io.StringIO()
        with patch.multiple(ff, OUT=out, STATUS_DIR=status, PROX=prox, TOURNAMENTS=tournaments):
            with patch.object(ff, "_transport_get", side_effect=transport) as request:
                with contextlib.redirect_stdout(log):
                    promoted = ff.main()
        text = (status / "sofa.json").read_text(encoding="utf-8")
        return promoted, json.loads(text), text, log.getvalue(), request, pointer, out

    def test_challenge_run_one_request_one_failure_rest_skipped(self):
        """Espelha a rodada de 27/09: 1 requisição, 1 liga falha, o resto não consultado."""
        resp = FakeResponse(403, CHALLENGE, {"content-type": "application/json", "server": "Varnish"})
        tournaments = list(ff.TOURNAMENTS)
        promoted, st, text, log, request, pointer, out = self._run_main(tournaments, lambda url, px: resp)

        self.assertFalse(promoted)
        self.assertFalse(st["ok"])
        self.assertEqual(1, request.call_count)
        t = st["transport"]
        self.assertEqual(1, t["requests"])
        self.assertEqual(0, t["direct_attempts"])
        self.assertEqual([tournaments[0][1]], t["failed_tournaments"])
        self.assertEqual({tournaments[0][1]: "recusa_destino"}, t["failure_classes"])
        self.assertEqual([lb for _, lb in tournaments[1:]], t["skipped_after_circuit"])
        self.assertEqual("challenge", t["last_refusal"]["reason"])
        self.assertEqual("recusa_destino", st["error_class"])
        self.assertNotEqual("Geo", st["error_class"])
        self.assertIn(f"nao_consultados={len(tournaments) - 1}", st["error"])
        self.assertIn("recusa=403:challenge@proxy", st["error"])
        self.assertEqual(1, log.count("falha de transporte"))
        self.assertEqual(len(tournaments) - 1, log.count("não consultado (circuito aberto)"))
        # ponteiro anterior preservado, nenhuma captura nova aparente
        self.assertEqual(pointer, json.loads((out / "sofa_latest.json").read_text(encoding="utf-8")))
        self.assertTrue(st["pointer_valid"])
        self.assertEqual(40, st["pointer_n"])
        for blob in (text, log):
            self.assertNotIn(SECRET_PASS, blob)
            self.assertNotIn(SECRET_USER, blob)

    def test_consecutive_timeouts_open_circuit_with_timeout_cause(self):
        tournaments = [(i, f"L{i}") for i in range(1, 8)]
        promoted, st, text, log, request, _, _ = self._run_main(
            tournaments, TimeoutError("read timed out"))
        n = ff.FALHAS_ATE_ABRIR
        self.assertFalse(promoted)
        t = st["transport"]
        self.assertEqual([f"L{i}" for i in range(1, n + 1)], t["failed_tournaments"])
        self.assertEqual({f"L{i}": "timeout" for i in range(1, n + 1)}, t["failure_classes"])
        self.assertEqual([f"L{i}" for i in range(n + 1, 8)], t["skipped_after_circuit"])
        self.assertEqual("timeout", t["circuit_cause"])
        self.assertEqual("timeout", st["error_class"])
        # 2 tentativas por liga consultada, como antes; nenhuma nas não consultadas
        self.assertEqual(2 * n, request.call_count)
        self.assertEqual(2 * n, t["requests"])
        self.assertEqual({"type": "TimeoutError", "classe": "timeout", "rota": "proxy"},
                         t["last_exception"])

    def test_sporadic_failure_without_circuit_uses_last_failed_class(self):
        good_seasons = FakeResponse(200, json.dumps({"seasons": []}).encode())

        def transport(url, px):
            if "/unique-tournament/2/" in url:
                return FakeResponse(503, b"<html>bad gateway</html>")
            return good_seasons

        promoted, st, *_ = self._run_main([(1, "A"), (2, "B"), (3, "C")], transport)
        t = st["transport"]
        self.assertFalse(promoted)
        self.assertFalse(t["circuit_open"])
        self.assertEqual(["B"], t["failed_tournaments"])
        self.assertEqual([], t["skipped_after_circuit"])
        self.assertEqual(["A", "C"], t["inactive_tournaments"])
        # 'seasons': [] é vazio LEGÍTIMO — não é erro de schema
        self.assertEqual([], t["schema_misses"])
        self.assertEqual("transporte", st["error_class"])

    def test_inactive_404_is_recorded_but_not_a_failure(self):
        def transport(url, px):
            if url.endswith("/seasons"):
                return FakeResponse(200, json.dumps({"seasons": [{"id": 9}]}).encode())
            return FakeResponse(404, b'{"error": {"code": 404, "reason": "Not Found"}}')

        _, st, *_ = self._run_main([(1, "A")], transport)
        t = st["transport"]
        self.assertEqual(["A"], t["inactive_tournaments"])
        self.assertEqual([], t["failed_tournaments"])
        self.assertEqual(404, t["last_refusal"]["status"])
        self.assertEqual("not found", t["last_refusal"]["reason"])

    # ── 200 com JSON válido SEM a chave esperada (revisão adversarial 30/09): o fluxo
    # (INATIVO / fim de página / promoção) fica como estava — só passa a ficar visível.
    @staticmethod
    def _events(utid, n, has_next=False):
        now = int(datetime.now(timezone.utc).timestamp())
        return {"events": [{"id": utid * 1000 + i, "startTimestamp": now + 3600 * (i + 1),
                            "homeTeam": {"name": f"H{utid}-{i}", "id": 1},
                            "awayTeam": {"name": f"A{utid}-{i}", "id": 2},
                            "tournament": {"name": f"Liga {utid}"}} for i in range(n)],
                "hasNextPage": has_next}

    def _ok(self, obj):
        return FakeResponse(200, json.dumps(obj).encode(), {"content-type": "application/json"})

    def test_seasons_200_without_key_is_recorded_and_flow_is_unchanged(self):
        def transport(url, px):
            if "/unique-tournament/2/seasons" in url:
                return FakeResponse(200, CHALLENGE, {"content-type": "application/json"})
            if url.endswith("/seasons"):
                return self._ok({"seasons": [{"id": 9}]})
            return self._ok(self._events(int(url.split("/unique-tournament/")[1].split("/")[0]), 8))

        promoted, st, text, log, request, _, _ = self._run_main(
            [(1, "A"), (2, "B"), (3, "C")], transport, prox=None)
        t = st["transport"]
        # comportamento de ANTES preservado (decisão de tratar como falha é do Diego)
        self.assertTrue(promoted)
        self.assertEqual(16, st["n_fixtures"])
        self.assertEqual(["B"], t["inactive_tournaments"])
        self.assertEqual([], t["failed_tournaments"])
        self.assertIsNone(st["error_class"])
        self.assertEqual(5, request.call_count)          # nenhuma requisição a mais
        # mas agora o fato fica registrado e o log não mente "sem temporada ativa"
        self.assertEqual([{
            "label": "B", "expected": "seasons", "classe": "schema",
            "endpoint": "api.sofascore.com/api/v1/unique-tournament/2/seasons",
            "json_type": "dict", "keys": ["error"], "reason": "challenge",
        }], t["schema_misses"])
        b_lines = [ln for ln in log.splitlines() if ln.startswith("[sofa] B ")]
        self.assertEqual(1, len(b_lines), log)
        self.assertIn("200 SEM 'seasons' válido", b_lines[0])
        self.assertIn("reason=challenge", b_lines[0])
        self.assertNotIn("sem temporada ativa", b_lines[0])

    def test_seasons_first_without_id_is_schema_but_empty_list_is_not(self):
        def transport(url, px):
            if "/unique-tournament/1/seasons" in url:
                return self._ok({"seasons": [{"name": "sem id"}]})
            return self._ok({"seasons": []})

        _, st, *_ = self._run_main([(1, "A"), (2, "B")], transport, prox=None)
        t = st["transport"]
        self.assertEqual(["A", "B"], t["inactive_tournaments"])
        self.assertEqual([("A", "seasons")], [(m["label"], m["expected"]) for m in t["schema_misses"]])

    def test_events_200_without_key_page0_and_partial_page1(self):
        def transport(url, px):
            if url.endswith("/seasons"):
                return self._ok({"seasons": [{"id": 9}]})
            utid = int(url.split("/unique-tournament/")[1].split("/")[0])
            page = int(url.rsplit("/", 1)[1])
            if utid == 1:                                  # página 0 já vem sem 'events'
                return self._ok({"error": {"code": 403, "reason": "challenge"}})
            if page == 0:                                  # parcial: 1ª página cheia...
                return self._ok(self._events(2, 12, has_next=True))
            return self._ok({"hasNextPage": False})        # ...2ª sem 'events'

        _, st, text, log, request, _, _ = self._run_main([(1, "A"), (2, "B")], transport, prox=None)
        t = st["transport"]
        self.assertEqual(5, request.call_count)
        self.assertEqual([], t["failed_tournaments"])     # fluxo de antes: break, não falha
        self.assertEqual([("A", "events", 0, "challenge", ["error"]),
                          ("B", "events", 1, None, ["hasNextPage"])],
                         [(m["label"], m["expected"], m["page"], m["reason"], m["keys"])
                          for m in t["schema_misses"]])
        self.assertIn("[sofa] B utid=2 season=9: 12 eventos", log)
        self.assertEqual(2, log.count("200 SEM 'events' válido"))

    def test_json_list_on_200_is_a_schema_failure(self):
        def transport(url, px):
            if "/unique-tournament/1/" in url:
                return self._ok([1, 2, 3])
            return self._ok({"seasons": []})

        _, st, *_ = self._run_main([(1, "A"), (2, "B")], transport, prox=None)
        t = st["transport"]
        self.assertEqual(["A"], t["failed_tournaments"])   # igual a antes: falha
        self.assertEqual({"A": "schema"}, t["failure_classes"])
        self.assertEqual([{"label": "A", "expected": "seasons", "json_type": "list", "keys": []}],
                         [{k: m[k] for k in ("label", "expected", "json_type", "keys")}
                          for m in t["schema_misses"]])
        self.assertEqual("schema", st["error_class"])

    def test_all_schema_misses_label_the_unhealthy_run_as_schema(self):
        _, st, *_ = self._run_main([(1, "A"), (2, "B"), (3, "C")],
                                   lambda url, px: FakeResponse(200, CHALLENGE), prox=None)
        self.assertFalse(st["ok"])
        self.assertFalse(st["promoted"])
        self.assertEqual("schema", st["error_class"])
        self.assertIn("esquema=3", st["error"])
        self.assertEqual(["A", "B", "C"], st["transport"]["inactive_tournaments"])

    def test_schema_keys_are_sanitized_and_recorder_never_raises(self):
        data = {f"k{i}:x@y<z>": 1 for i in range(15)}
        data["éç"] = 1
        rec = ff._record_schema_miss("L", SEASONS_URL + "?t=1", data, "seasons")
        self.assertEqual(10, len(rec["keys"]))
        for k in rec["keys"]:
            self.assertRegex(k, r"^[A-Za-z0-9_ -]{1,40}$")
        self.assertEqual("api.sofascore.com/api/v1/unique-tournament/325/seasons", rec["endpoint"])
        with patch.object(ff, "_endpoint", side_effect=RuntimeError("quebra")):
            rec = ff._record_schema_miss("M", SEASONS_URL, {"x": 1}, "events", 2)
        self.assertEqual({"label": "M", "expected": "events", "classe": "schema"}, rec)
        self.assertEqual(2, len(ff._GET_DIAG["schema_misses"]))

    def test_client_versions_are_stamped(self):
        _, st, *_ = self._run_main([(1, "A")], lambda url, px: FakeResponse(403, CHALLENGE))
        client = st["client"]
        self.assertEqual("curl_cffi" if ff._HTTP_IMPERSONATE else "requests", client["lib"])
        try:
            import curl_cffi
            self.assertEqual(curl_cffi.__version__, client["curl_cffi"])
        except ImportError:
            self.assertIsNone(client["curl_cffi"])
        try:
            import requests
            self.assertEqual(requests.__version__, client["requests"])
        except ImportError:
            pass
        self.assertEqual(
            hashlib.sha256(json.dumps(ff.H, sort_keys=True).encode("utf-8")).hexdigest()[:16],
            client["headers_sha16"])


class ContractUnchangedTest(OfflineTestCase):
    def test_request_contract_is_the_same(self):
        """Headers, UA, impersonate e timeout: o patch de observabilidade não mexe."""
        self.assertEqual({"x-requested-with": "XMLHttpRequest", "User-Agent": "Mozilla/5.0",
                          "Accept": "application/json"}, ff.H)
        self.assertEqual(20, ff.REQUEST_TIMEOUT)
        captured = {}

        def fake_get(url, **kwargs):
            captured.update(kwargs)
            return FakeResponse(200, b"{}")

        with patch.object(ff._HTTP_CLIENT, "get", side_effect=fake_get):
            ff._transport_get(SEASONS_URL, PROX)
        self.assertIs(ff.H, captured["headers"])
        self.assertEqual(ff.REQUEST_TIMEOUT, captured["timeout"])
        self.assertEqual(PROX, captured["proxies"])
        self.assertEqual(ff._client_info()["impersonate"], captured.get("impersonate"))

    def test_houses_keep_their_classification(self):
        """A classe própria é só da Sofa: classify_error das casas continua igual."""
        self.assertEqual("Geo", cc.classify_error("HTTP 403 via proxy"))
        self.assertEqual("Timeout", cc.classify_error(TimeoutError("x")))
        self.assertEqual("HTTP429", cc.classify_error("HTTP 429"))

    def test_sofa_class_for_crash_path_uses_common_rule(self):
        self.assertEqual("Timeout", ff._sofa_error_class(TimeoutError("x")))
        self.assertIsNone(ff._sofa_error_class(None))


if __name__ == "__main__":
    unittest.main()
