# -*- coding: utf-8 -*-
"""§8 — publicação atômica: manifesto único por build + gate fail-closed no deploy.

O incidente da auditoria: board fresco publicado junto com history/moves/openclose atrasados.
O manifesto amarra os 5 artefatos a UM build; o deploy bloqueia se algum faltar, for de outro
build (hash), estiver velho, ou se o histórico válido encolher sem migração aprovada.
"""
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import build_manifest
import deploy
from manifest_common import ARTIFACTS, MANIFEST_PREFIX, parse_manifest_text, sha256_bytes

BRT = timezone(timedelta(hours=-3))


def _window(prefix, obj):
    return prefix + json.dumps(obj, ensure_ascii=False) + ";"


def _fake_valor(dirpath, gerado_iso=None):
    """Escreve os 5 artefatos com JSON válido e gerado_iso recente."""
    gi = gerado_iso or datetime.now(BRT).isoformat(timespec="seconds")
    data = dirpath / "data"
    data.mkdir(parents=True, exist_ok=True)
    payloads = {
        "board": {"gerado_iso": gi, "jogos": [{"inicio_iso": gi}] * 3},
        "ops": {"gerado_iso": gi, "casas": [{"id": "betano"}] * 7},
        "history": {"gerado_iso": gi, "banco": {"liquidadas": 100, "clv_validas": 40},
                    "liquidadas": []},
        "openclose": {"gerado_iso": gi, "rows": [{"gid": "sofa:1", "kickoff": gi}]},
        "moves": {"sofa:1|Faltas|10.5|over": {"betano": [[1, 1.9], [2, 1.95]]}},
    }
    for rel, prefix, name in ARTIFACTS:
        (dirpath / rel.lstrip("/")).write_text(_window(prefix, payloads[name]),
                                               encoding="utf-8")


def _write_manifest_for(dirpath, generated_iso=None, valid_count=40):
    arts = {}
    for rel, _p, name in ARTIFACTS:
        f = dirpath / rel.lstrip("/")
        arts[rel] = {"name": name, "sha256": sha256_bytes(f.read_bytes()),
                     "valid_count": valid_count}
    man = {"manifest_version": 1, "build_id": "b" * 12,
           "generated_iso": generated_iso or datetime.now(BRT).isoformat(timespec="seconds"),
           "artifacts": arts}
    (dirpath / "data" / "manifest.js").write_text(
        MANIFEST_PREFIX + json.dumps(man, ensure_ascii=False) + ";", encoding="utf-8")
    return man


class BuildManifestTests(unittest.TestCase):
    def test_build_manifest_ok_and_gate_passes(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            with patch_valor(valor):
                self.assertEqual(build_manifest.main(), 0)
            man = parse_manifest_text((valor / "data" / "manifest.js").read_text(encoding="utf-8"))
            self.assertEqual(len(man["artifacts"]), 5)
            self.assertEqual(man["artifacts"]["/data/history.js"]["valid_count"], 40)
            # o gate do deploy aceita esse build coerente
            with patch_live(None):
                self.assertIsNone(deploy.manifest_gate(valor))

    def test_build_manifest_blocks_stale_artifact(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            # openclose de 2h atrás → spread > 45min → build misturado
            old = (datetime.now(BRT) - timedelta(hours=2)).isoformat(timespec="seconds")
            rel = "/data/openclose.js"
            (valor / rel.lstrip("/")).write_text(
                _window("window.OPENCLOSE=", {"gerado_iso": old, "rows": []}), encoding="utf-8")
            with patch_valor(valor):
                self.assertEqual(build_manifest.main(), 1)

    def test_build_manifest_blocks_missing_artifact(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            (valor / "data" / "openclose.js").unlink()
            with patch_valor(valor):
                self.assertEqual(build_manifest.main(), 1)


class ManifestGateTests(unittest.TestCase):
    def test_missing_manifest_blocks(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            with patch_live(None):
                self.assertIn("manifesto ausente", deploy.manifest_gate(valor))

    def test_stale_manifest_blocks(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            old = (datetime.now(BRT) - timedelta(hours=99)).isoformat(timespec="seconds")
            _write_manifest_for(valor, generated_iso=old)
            with patch_live(None):
                self.assertIn("velho", deploy.manifest_gate(valor))

    def test_hash_mismatch_blocks_other_build(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            _write_manifest_for(valor)
            # troca 1 artefato DEPOIS do manifesto = artefato de outro build
            (valor / "data" / "moves.js").write_text("window.MOVES={};", encoding="utf-8")
            with patch_live(None):
                self.assertIn("hash divergente", deploy.manifest_gate(valor))

    def test_fresh_consistent_passes(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            _write_manifest_for(valor)
            with patch_live(None):
                self.assertIsNone(deploy.manifest_gate(valor))

    def test_history_shrink_blocks_without_approval(self):
        with TemporaryDirectory() as td:
            valor = Path(td) / "valor"
            _fake_valor(valor)
            man = _write_manifest_for(valor, valid_count=10)  # local: 10 válidas
            live = dict(man)
            live_arts = json.loads(json.dumps(man["artifacts"]))
            live_arts["/data/history.js"]["valid_count"] = 100  # produção: 100
            live["artifacts"] = live_arts
            with patch_live("https://valor.example"):
                with FakeLiveManifest(live):
                    reason = deploy.manifest_gate(valor)
            self.assertIsNotNone(reason)
            self.assertIn("encolheu", reason)


# ---- helpers de patch (contextmanagers leves) ----
class patch_valor:
    def __init__(self, valor): self.valor = valor
    def __enter__(self):
        self._old = build_manifest.VALOR
        build_manifest.VALOR = self.valor
    def __exit__(self, *a):
        build_manifest.VALOR = self._old


class patch_live:
    def __init__(self, base): self.base = base
    def __enter__(self):
        self._old = deploy.DEPLOY_LIVE_BASE
        deploy.DEPLOY_LIVE_BASE = self.base
    def __exit__(self, *a):
        deploy.DEPLOY_LIVE_BASE = self._old


class FakeLiveManifest:
    """Substitui deploy._fetch_text pela produção 'ao vivo' simulada."""
    def __init__(self, live_manifest):
        self.txt = MANIFEST_PREFIX + json.dumps(live_manifest, ensure_ascii=False) + ";"
    def __enter__(self):
        self._old = deploy._fetch_text
        deploy._fetch_text = lambda url, timeout=20: self.txt
    def __exit__(self, *a):
        deploy._fetch_text = self._old


if __name__ == "__main__":
    unittest.main()
