# -*- coding: utf-8 -*-
"""Testes do contrato fail-closed do publicador Netlify."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deploy


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name)
        for index, rel in enumerate(sorted(deploy.CRITICAL_FILES)):
            path = self.site / rel.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"arquivo-{index}", encoding="utf-8")
        self.token = patch.object(deploy, "TOKEN", "test-token")
        self.directory = patch.object(deploy, "DIR", self.site)
        self.sleep = patch.object(deploy.time, "sleep", lambda _seconds: None)
        self.token.start()
        self.directory.start()
        self.sleep.start()
        self.addCleanup(self.token.stop)
        self.addCleanup(self.directory.stop)
        self.addCleanup(self.sleep.stop)

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
