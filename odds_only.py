"""Verified snapshot-only contingency. No network, models, Sofa IDs or fuzzy joins.

A house/event remains isolated. Missing, malformed or old evidence is never
replaced with a processing clock. The gate reconstructs this exact projection
from the pointer and immutable JSONL bytes before allowing publication.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import unicodedata

from bookmaker_contracts import normalize_betano_markets, normalize_7k_event_name

MAX_AGE_MINUTES = 120
MAX_FUTURE_SECONDS = 300
KICKOFF_GUARD_SECONDS = 300
MAX_RAW_BYTES = 8 * 1024 * 1024
UTC = timezone.utc
BRT = timezone(timedelta(hours=-3))
HOUSES = {
    "betano": "Betano", "superbet": "Superbet", "estrelabet": "EstrelaBet",
    "7k": "7k", "pinnacle": "Pinnacle", "bet365": "bet365",
    "sportingbet": "Sportingbet",
}
MARKETS = (
    "Cartões", "Faltas", "Finalizações", "Chutes no gol", "Escanteios",
    "Impedimentos", "Laterais", "Tiros de meta", "Desarmes",
)
QUOTE_META = (
    "market_type_id", "market_type_name", "source_market", "source_tab",
    "market_family", "settlement", "period", "scope", "unit", "units",
)
# These precise collectors call datetime.now(BRT), then strftime without an
# offset. This is an audited serializer contract, NOT a timezone heuristic:
# Superbet _fetch_event -> normalize_event; Pinnacle/EstrelaBet main(). New or
# unknown collectors and ALL kickoff strings still require an explicit offset.
LEGACY_BRT_COLLECTORS = frozenset({"superbet", "pinnacle", "estrelabet"})
LEGACY_POINTER_MAX_DELTA_SECONDS = 15 * 60


class OddsOnlyUnavailable(ValueError):
    """No nonempty, verified and unexpired odds-only board can be built."""


class EvidenceError(ValueError):
    """Public-safe reason code; never includes raw paths, exceptions or secrets."""


def _iso(dt):
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _fold(text):
    # Cosmetic normalization only: no aliases, club-word removal or fuzzy match.
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _clock(value):
    """Only explicit timezone/epoch clocks. Never infer timezone from the host."""
    if isinstance(value, bool) or value is None:
        raise EvidenceError("invalid_clock")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise EvidenceError("invalid_clock")
        stamp = float(value)
        if stamp > 1e11:
            stamp /= 1000
        try:
            return datetime.fromtimestamp(stamp, UTC)
        except (ValueError, OverflowError, OSError):
            raise EvidenceError("invalid_clock") from None
    if not isinstance(value, str):
        raise EvidenceError("invalid_clock")
    text = value.strip()
    # .NET timestamps are explicit milliseconds since the UTC epoch.
    dotnet = re.fullmatch(r"/Date\((\d{12,13})\)/", text)
    if dotnet:
        return _clock(int(dotnet.group(1)))
    if re.fullmatch(r"\d{10}|\d{13}", text):
        return _clock(int(text))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise EvidenceError("invalid_clock") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise EvidenceError("clock_without_timezone")
    return dt.astimezone(UTC)


def _now(value):
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value.astimezone(UTC)
    return _clock(value)


def _fresh(value, now):
    dt = _clock(value)
    if dt > now + timedelta(seconds=MAX_FUTURE_SECONDS):
        raise EvidenceError("future_capture")
    if now >= dt + timedelta(minutes=MAX_AGE_MINUTES):
        raise EvidenceError("expired_capture")
    return dt


def _captured(value, proof, now):
    source = "source_captured_at"
    if (isinstance(value, str)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value)
            and proof["house"] in LEGACY_BRT_COLLECTORS):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRT)
        except ValueError:
            raise EvidenceError("invalid_clock") from None
        captured = _fresh(_iso(parsed), now)
        if abs((captured - _clock(proof["pointer_at"])).total_seconds()) > LEGACY_POINTER_MAX_DELTA_SECONDS:
            raise EvidenceError("legacy_clock_inconsistent_with_pointer")
        source = "legacy_collector_brt"
    else:
        captured = _fresh(value, now)
    return captured, source


def _bad_constant(value):
    raise EvidenceError("nonfinite_json")


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise EvidenceError("duplicate_json_key")
        out[key] = value
    return out


def _load_json(raw):
    try:
        data = json.loads(raw, parse_constant=_bad_constant, object_pairs_hook=_unique_object)
        _finite_tree(data)
        return data
    except EvidenceError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EvidenceError("invalid_json") from None


def _finite_tree(obj):
    if isinstance(obj, float) and not math.isfinite(obj):
        raise EvidenceError("nonfinite_json")
    if isinstance(obj, dict):
        for value in obj.values():
            _finite_tree(value)
    elif isinstance(obj, list):
        for value in obj:
            _finite_tree(value)


def _safe_file(root, relative, limit):
    if not isinstance(relative, str) or "\\" in relative:
        raise EvidenceError("unsafe_path")
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(x in (".", "..") for x in parts):
        raise EvidenceError("unsafe_path")
    base = Path(root)
    if base.is_symlink():
        raise EvidenceError("symlink_path")
    base = base.resolve()
    target = base
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise EvidenceError("symlink_path")
    try:
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(base) or not resolved.is_file():
            raise EvidenceError("unsafe_path")
        if resolved.stat().st_size > limit:
            raise EvidenceError("file_too_large")
        # Do not follow a final-component symlink introduced after the checks.
        fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise EvidenceError("file_too_large")
        return raw
    except EvidenceError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise EvidenceError("missing_or_unreadable_file") from None


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise EvidenceError("invalid_event_id")
    value = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise EvidenceError("invalid_event_id")
    return value


def _text(value, limit=300):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EvidenceError("invalid_identity")
    if any(ord(c) < 32 for c in value):
        raise EvidenceError("invalid_identity")
    return value.strip()


def _read_house(root, slug, now):
    pointer_path = f"data/odds/{slug}_latest_full.json"
    pointer_raw = _safe_file(root, pointer_path, 65536)
    pointer = _load_json(pointer_raw)
    if not isinstance(pointer, dict) or pointer.get("mode") != "full":
        raise EvidenceError("not_full_pointer")
    pointer_at = _fresh(pointer.get("at"), now)
    if "casa" in pointer and pointer["casa"] not in (slug, HOUSES[slug]):
        raise EvidenceError("wrong_house_pointer")
    n = pointer.get("n")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise EvidenceError("invalid_pointer_count")
    relative = pointer.get("file")
    if (not isinstance(relative, str) or not relative.endswith(".jsonl")
            or PurePosixPath(relative).is_absolute()):
        raise EvidenceError("invalid_snapshot_path")
    # Source basename is bound to the house, even for otherwise valid JSONL.
    basename = PurePosixPath(relative).name
    if not basename.startswith(slug + "_"):
        raise EvidenceError("wrong_house_snapshot")
    raw = _safe_file(root, "data/odds/" + relative, MAX_RAW_BYTES)
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != n:
        raise EvidenceError("pointer_count_mismatch")
    records = []
    ids = set()
    for line in lines:
        record = _load_json(line)
        if not isinstance(record, dict):
            raise EvidenceError("invalid_event_record")
        house = record.get("casa")
        if house is None and slug == "betano" and isinstance(record.get("markets"), dict):
            pass  # Raw Betano contract has no 'casa'; its house-bound file does.
        elif not isinstance(house, str) or _fold(house) not in {_fold(slug), _fold(HOUSES[slug])}:
            raise EvidenceError("wrong_house_record")
        event_id = _id(record.get("event_id"))
        if event_id in ids:
            raise EvidenceError("duplicate_event_identity")
        ids.add(event_id)
        records.append((record, hashlib.sha256(line).hexdigest()))
    proof = {
        "house": slug, "pointer": pointer_path, "snapshot": "data/odds/" + relative,
        "pointer_at": _iso(pointer_at),
        "pointer_sha256": hashlib.sha256(pointer_raw).hexdigest(),
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return records, proof


def _number(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise EvidenceError("invalid_quote")
    if not math.isfinite(value) or value < lower or value > upper:
        raise EvidenceError("invalid_quote")
    return float(value)


def _quotes(rows, captured, now, expected_scope):
    if not isinstance(rows, list):
        return [], []
    out, clocks, seen = [], [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = " ".join(str(row.get(k) or "") for k in ("market_type_name", "source_market", "market_family"))
        label = unicodedata.normalize("NFD", label).casefold()
        label = "".join(c for c in label if unicodedata.category(c) != "Mn")
        if row.get("three_way") or any(row.get(k) is not None for k in (
                "exact", "exactly", "equal", "draw", "outcomes", "selections")):
            continue  # UI only supports a two-way O/U pair, never hide a third outcome.
        if re.search(r"(?:3|three)[ _-]*(?:way|vias)|tres\s+vias|\b(?:half|quarter|1[ºo]?\s+tempo|2[ºo]?\s+tempo)\b", label):
            continue
        if row.get("scope") not in (None, expected_scope):
            continue
        if row.get("period") not in (None, 0, "0", "full", "full_time", "FT", "match"):
            continue
        try:
            line = _number(row.get("linha"), 0, 1000)
            over = _number(row.get("over"), 1.01, 50)
            under = _number(row.get("under"), 1.01, 50)
            if over <= 1.01 or under <= 1.01 or not math.isclose(line * 2, round(line * 2), abs_tol=1e-9):
                continue
            clock = captured
            for key in ("captured_at", "observed_at"):
                if key in row:
                    clock = min(clock, _fresh(row[key], now))
            cleaned = {"linha": line, "over": over, "under": under}
            for key in QUOTE_META:
                value = row.get(key)
                if value is not None:
                    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                        raise EvidenceError("invalid_quote_metadata")
                    if isinstance(value, str) and len(value) > 400:
                        raise EvidenceError("invalid_quote_metadata")
                    cleaned[key] = value
            if line in seen:
                # Two source contracts at the same number are ambiguous; reject
                # the whole ladder rather than arbitrarily picking one.
                return [], []
            seen.add(line)
            cleaned["captured_at"] = _iso(clock)
            cleaned["expires_at"] = _iso(clock + timedelta(minutes=MAX_AGE_MINUTES))
            out.append(cleaned)
            clocks.append(clock)
        except EvidenceError:
            continue
    return sorted(out, key=lambda r: r["linha"]), clocks


def _game(record, proof, row_sha, now, off):
    if any(record.get(key) for key in ("_stale", "stale", "stale_keep", "is_stale")):
        raise EvidenceError("stale_record")
    captured, timestamp_source = _captured(record.get("captured_at"), proof, now)
    if (record.get("is_live") is True or record.get("live") is True
            or record.get("game_state") in {"started", "live", "finished", "cancelled", "suspended"}):
        raise EvidenceError("not_pregame")
    kickoff = _clock(record.get("start"))
    if kickoff <= now + timedelta(seconds=KICKOFF_GUARD_SECONDS):
        raise EvidenceError("started_or_imminent")
    slug = proof["house"]
    name = _text(record.get("name"))
    if slug == "7k":
        name = normalize_7k_event_name(name)
    parts = re.split(r"\s+-\s+|\s+vs\.?\s+", name)
    if len(parts) != 2 or not all(parts) or _fold(parts[0]) == _fold(parts[1]):
        raise EvidenceError("ambiguous_participants")
    home, away = map(str.strip, parts)
    league = _text(record.get("league"))
    if slug == "betano":
        markets, teams = normalize_betano_markets(record)
    else:
        markets, teams = record.get("mercados") or {}, record.get("mercados_time") or {}
    if not isinstance(markets, dict) or not isinstance(teams, dict):
        raise EvidenceError("invalid_markets_schema")
    house = HOUSES[slug]
    total, sides, clocks = {}, {}, []
    for market in MARKETS:
        if _fold(market) in off:
            continue
        rows, times = _quotes(markets.get(market), captured, now, "match")
        if rows:
            total[market] = {house: rows}
            clocks.extend(times)
        by_team = teams.get(market) or {}
        if not isinstance(by_team, dict):
            continue
        assignments = {}
        for team, rows in by_team.items():
            if not isinstance(team, str):
                continue
            hits = [side for side, name in (("home", home), ("away", away)) if _fold(team) == _fold(name)]
            if len(hits) != 1:
                continue
            side = hits[0]
            if side in assignments:
                assignments[side] = None  # Duplicate cosmetic aliases are ambiguous.
            else:
                assignments[side] = rows
        for side, rows in assignments.items():
            lines, times = _quotes(rows, captured, now, "team")
            if lines:
                sides.setdefault(market, {})[side] = {
                    "nome": home if side == "home" else away, "casas": {house: lines},
                }
                clocks.extend(times)
    if not total and not sides:
        raise EvidenceError("no_supported_quotes")
    clock = min(clocks)
    return {
        "jogo": f"{home} - {away}", "liga": league,
        "inicio": kickoff.astimezone(BRT).strftime("%d/%m %H:%M"),
        "inicio_iso": _iso(kickoff), "home": home, "away": away,
        "casas": [house], "mercados": total, "times": sides, "valor": [],
        "tem_valor": False, "n_mercados": len(set(total) | set(sides)),
        "game_state": "upcoming", "captured_at": _iso(clock),
        "expires_at": _iso(clock + timedelta(minutes=MAX_AGE_MINUTES)),
        "source_identity": dict(proof, event_id=_id(record.get("event_id")),
                                row_sha256=row_sha, timestamp_source=timestamp_source),
    }


def build_odds_only(root, now=None, reason="Calendário estatístico indisponível; somente odds verificadas"):
    now = _now(now)
    off = {_fold(x) for x in os.environ.get("MERCADOS_OFF", "").split(",") if x.strip()}
    games, excluded = [], []
    for slug, house in HOUSES.items():
        try:
            records, proof = _read_house(root, slug, now)
        except EvidenceError as exc:
            excluded.append({"house": house, "reason": str(exc)})
            continue
        accepted = []
        for record, row_sha in records:
            try:
                accepted.append(_game(record, proof, row_sha, now, off))
            except (EvidenceError, ValueError, TypeError, OverflowError, AttributeError):
                # A record can legitimately be started/old; it must never be
                # refreshed from the current pointer or another game's odds.
                continue
        if not accepted:
            excluded.append({"house": house, "reason": "no_eligible_events"})
        games.extend(accepted)
    if not games:
        raise OddsOnlyUnavailable("Nenhuma casa com evento e odds atuais verificáveis")
    games.sort(key=lambda g: (g["inicio_iso"], g["source_identity"]["house"], g["source_identity"]["event_id"]))
    oldest = min(_clock(g["captured_at"]) for g in games)
    houses = [house for house in HOUSES.values() if any(house in g["casas"] for g in games)]
    markets = [m for m in MARKETS if any(m in g["mercados"] or m in g["times"] for g in games)]
    return {
        "mode": "odds_only", "gerado": oldest.astimezone(BRT).strftime("%Y-%m-%d %H:%M"),
        "gerado_iso": _iso(oldest), "rebuilt_at": _iso(now),
        "casas": houses, "mercados": markets, "fonte": "verified_bookmaker_snapshots",
        "jogos": games, "model": {"status": "unavailable", "source": "none", "markets": []},
        "pricing": {}, "contingency": {
            "reason": str(reason)[:500], "max_age_minutes": MAX_AGE_MINUTES,
            "expires_at": min(g["expires_at"] for g in games),
            "houses": houses, "excluded": excluded,
        },
    }


def odds_only_reasons(board, root, now=None):
    """Fail closed when quotes expire or the exact source projection differs."""
    now = _now(now)
    if not isinstance(board, dict) or board.get("mode") != "odds_only":
        return ["Modo de contingência inválido"]
    try:
        _finite_tree(board)
        rebuilt = _fresh(board.get("rebuilt_at"), now)
        if _clock((board.get("contingency") or {}).get("expires_at")) <= now:
            return ["Contingência expirada"]
        expected = build_odds_only(root, now, (board.get("contingency") or {}).get("reason", ""))
        actual = dict(board)
        actual.pop("rebuilt_at", None)
        expected.pop("rebuilt_at", None)
        if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
            return ["Contingência diverge das fontes verificadas (identidade, odds, relógios ou metadados)"]
        if rebuilt < _clock(board["gerado_iso"]) - timedelta(seconds=MAX_FUTURE_SECONDS):
            return ["Relógio de reconstrução incompatível com a captura"]
    except (EvidenceError, OddsOnlyUnavailable, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return ["Não foi possível comprovar as fontes e relógios da contingência"]
    return []
