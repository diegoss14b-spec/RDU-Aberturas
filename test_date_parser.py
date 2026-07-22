# -*- coding: utf-8 -*-
"""§10 — parser único de datas: Z / -03:00 / -0300 / ingênuo, idêntico no py3.9 e 3.12.

O bug: datetime.fromisoformat do 3.9 rejeita 'Z' e offsets sem ':' ('-0300'), então a
MESMA pendência virava age=unknown no Mac (3.9) e idade válida no CI (3.12), escondendo o
backlog. parse_iso_flex normaliza antes do fromisoformat, então a classificação não depende
da versão do Python.
"""
import unittest
from datetime import datetime, timezone, timedelta

from history_quality import BRT, parse_iso_flex, parse_ts
from history_settle import _age_bucket, _parse_dt

UTC = timezone.utc


class ParserFlexTests(unittest.TestCase):
    def test_z_offsets_all_resolve_to_same_instant(self):
        base = "2026-07-20T18:30:00"
        z = parse_iso_flex(base + "Z")
        colon = parse_iso_flex(base + "+00:00")
        nocolon = parse_iso_flex(base + "+0000")
        for got in (z, colon, nocolon):
            self.assertIsNotNone(got)
            self.assertEqual(got.utcoffset(), timedelta(0))
            self.assertEqual(got.astimezone(UTC),
                             datetime(2026, 7, 20, 18, 30, tzinfo=UTC))

    def test_minus_0300_matches_minus_03_00(self):
        a = parse_iso_flex("2026-07-20T18:30:00-0300")
        b = parse_iso_flex("2026-07-20T18:30:00-03:00")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a, b)
        self.assertEqual(a.utcoffset(), timedelta(hours=-3))

    def test_space_separator_and_fraction(self):
        got = parse_iso_flex("2026-07-20 18:30:00.123456789-0300")
        self.assertIsNotNone(got)
        self.assertEqual(got.utcoffset(), timedelta(hours=-3))
        self.assertEqual(got.microsecond, 123456)

    def test_naive_stays_naive_unless_default_tz(self):
        naive = parse_iso_flex("2026-07-20T18:30:00")
        self.assertIsNotNone(naive)
        self.assertIsNone(naive.tzinfo)  # nunca chuta fuso em silêncio
        aware = parse_iso_flex("2026-07-20T18:30:00", default_tz=BRT)
        self.assertEqual(aware.utcoffset(), timedelta(hours=-3))

    def test_date_only_is_not_mistaken_for_offset(self):
        got = parse_iso_flex("2026-07-20")
        self.assertIsNotNone(got)
        self.assertEqual((got.year, got.month, got.day), (2026, 7, 20))

    def test_garbage_returns_none_not_recent(self):
        self.assertIsNone(parse_iso_flex("sem-data"))
        self.assertIsNone(parse_iso_flex(""))
        self.assertIsNone(parse_iso_flex(None))

    def test_parse_ts_alias_handles_z_and_nocolon(self):
        # antes retornava None no py3.9 pra 'Z' e '-0300'
        self.assertIsNotNone(parse_ts("2026-07-20T18:30:00Z"))
        self.assertIsNotNone(parse_ts("2026-07-20T18:30:00-0300"))


class SettleAgeTests(unittest.TestCase):
    """A classificação de idade do backlog não pode depender do formato do offset."""

    def setUp(self):
        self.now = datetime(2026, 7, 22, 12, 0, tzinfo=BRT)

    def test_settle_parse_dt_variants_equal(self):
        got = {
            "z": _parse_dt("2026-07-20T15:00:00Z"),
            "colon": _parse_dt("2026-07-20T12:00:00-03:00"),
            "nocolon": _parse_dt("2026-07-20T12:00:00-0300"),
        }
        for v in got.values():
            self.assertIsNotNone(v)
        # z 15:00Z == 12:00 BRT == colon == nocolon
        self.assertEqual(got["z"], got["colon"])
        self.assertEqual(got["colon"], got["nocolon"])

    def test_age_bucket_no_longer_unknown_for_offset_stamps(self):
        for stamp in ("2026-07-20T12:00:00-0300", "2026-07-20T15:00:00Z",
                      "2026-07-20 12:00:00-03:00"):
            bucket, age = _age_bucket(stamp, self.now)
            self.assertNotEqual(bucket, "unknown", stamp)
            self.assertIsNotNone(age)
        # genuinamente ilegível continua unknown (não vira "recente")
        bucket, age = _age_bucket("lixo", self.now)
        self.assertEqual(bucket, "unknown")
        self.assertIsNone(age)


if __name__ == "__main__":
    unittest.main()
