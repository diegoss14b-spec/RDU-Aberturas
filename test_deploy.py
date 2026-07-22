# -*- coding: utf-8 -*-
"""Testes do contrato fail-closed do publicador Netlify."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import deploy
from manifest_common import ARTIFACTS, MANIFEST_PREFIX, sha256_bytes

BRT = timezone(timedelta(hours=-3))


class DeployTests(unittest.TestCase):
    def _write_valid_manifest(self):
        """§8 — manifesto atômico fresco casando com os artefatos do diretório de teste.
        deploy.manifest_gate confere presença + sha256 dos 5 artefatos e o frescor."""
        artifacts = {}
        for rel, _prefix, name in ARTIFACTS:
            f = self.site / rel.lstrip("/")
            artifacts[rel] = {"name": name, "sha256": sha256_bytes(f.read_bytes()),
                              "valid_count": 1000}
        man = {
            "manifest_version": 1,
            "build_id": "testbuild0001",
            "generated_iso": datetime.now(BRT).isoformat(timespec="seconds"),
            "artifacts": artifacts,
        }
        (self.site / "data" / "manifest.js").write_text(
            MANIFEST_PREFIX + json.dumps(man, ensure_ascii=False) + ";", encoding="utf-8")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name)
        for index, rel in enumerate(sorted(deploy.CRITICAL_FILES)):
            path = self.site / rel.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"arquivo-{index}", encoding="utf-8")
        # index.html tem que passar o guard anti-stub (>15000 bytes com as 4 views)
        (self.site / "index.html").write_text(
            "<!doctype html><html><body>" + ("<!-- app da Mesa -->" * 1200)
            + 'js/board.js js/valor.js js/history.js js/ops.js</body></html>',
            encoding="utf-8")
        # manifesto atômico válido (por último — depende dos hashes dos artefatos acima)
        self._write_valid_manifest()
        self.token = patch.object(deploy, "TOKEN", "test-token")
        self.directory = patch.object(deploy, "DIR", self.site)
        self.sleep = patch.object(deploy.time, "sleep", lambda _seconds: None)
        # sem base ao vivo: a trava anti-encolhimento não faz rede nos testes
        self.live = patch.object(deploy, "DEPLOY_LIVE_BASE", None)
        self.token.start()
        self.directory.start()
        self.sleep.start()
        self.live.start()
        self.addCleanup(self.token.stop)
        self.addCleanup(self.directory.stop)
        self.addCleanup(self.sleep.stop)
        self.addCleanup(self.live.stop)

    def digest(self, rel):
        return hashlib.sha1((self.site / rel.lstrip("/")).read_bytes()).hexdigest()

    def test_missing_critical_file_blocks_before_api(self):
        (self.site / "data" / "board.js").unlink()
        with patch.object(deploy, "api") as api:
            self.assertEqual(deploy.main(), 1)
            api.assert_not_called()

    def test_missing_deploy_id_fails(self):
        with patch.object(deploy, "api", return_value={}):
            self.assertEqual(deploy.main(), 1)

    def test_required_upload_failure_fails_after_retries(self):
        required_sha = self.digest("/index.html")
        calls = {"put": 0}

        def fake_api(method, path, data=None, raw=False):
            if method == "POST":
                return {"id": "dep-1", "required": [required_sha]}
            if method == "PUT":
                calls["put"] += 1
                raise OSError("falha simulada")
            self.fail(f"chamada inesperada: {method} {path}")

        with patch.object(deploy, "api", fake_api):
            self.assertEqual(deploy.main(), 1)
        self.assertEqual(calls["put"], 3)

    def test_unknown_required_hash_fails_closed(self):
        def fake_api(method, path, data=None, raw=False):
            if method == "POST":
                return {"id": "dep-2", "required": ["hash-ausente"]}
            self.fail(f"chamada inesperada: {method} {path}")

        with patch.object(deploy, "api", fake_api):
            self.assertEqual(deploy.main(), 1)

    def test_timeout_is_failure(self):
        calls = {"get": 0}

        def fake_api(method, path, data=None, raw=False):
            if method == "POST":
                return {"id": "dep-3", "required": []}
            if method == "GET":
                calls["get"] += 1
                return {"state": "processing"}
            self.fail(f"chamada inesperada: {method} {path}")

        with patch.object(deploy, "api", fake_api):
            self.assertEqual(deploy.main(), 1)
        self.assertEqual(calls["get"], 40)

    def test_ready_deploy_succeeds(self):
        required_sha = self.digest("/index.html")
        calls = []

        def fake_api(method, path, data=None, raw=False):
            calls.append((method, path))
            if method == "POST":
                return {"id": "dep-4", "required": [required_sha]}
            if method == "PUT":
                return {}
            if method == "GET":
                return {"state": "ready", "ssl_url": "https://example.test"}
            self.fail(f"chamada inesperada: {method} {path}")

        with patch.object(deploy, "api", fake_api):
            self.assertEqual(deploy.main(), 0)
        self.assertTrue(any(method == "PUT" for method, _path in calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
