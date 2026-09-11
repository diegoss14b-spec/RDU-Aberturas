"""Explicit competition identity and bounded fair selection for BetsAPI.

Provider labels below are exact normalized aliases, never substring matches.
Unrecognized competitions remain eligible for the exploration budget.
"""
import re
import unicodedata


def norm(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()


PRIMARY = {norm(x) for x in (
    'England Premier League', 'Spain La Liga', 'Italy Serie A',
    'Germany Bundesliga I', 'Germany Bundesliga', 'France Ligue 1',
    'Brazil Serie A', 'Brazil Serie B', 'Argentina Liga Profesional',
    'Argentina Primera Division', 'Mexico Liga MX', 'Colombia Primera A',
    'Chile Liga de Primera', 'Chile Primera Division', 'Uruguay Primera Division',
    'Paraguay Division Profesional', 'Peru Liga 1', 'Ecuador LigaPro Serie A',
    'Bolivia Primera Division', 'Norway Eliteserien', 'China Super League',
    'Netherlands Eredivisie', 'Portugal Primeira Liga', 'USA MLS',
    'UEFA Champions League', 'UEFA Europa League', 'UEFA Conference League',
    'UEFA Europa Conference League', 'Copa Libertadores', 'Copa Sudamericana',
)}
SECONDARY = {norm(x) for x in (
    'England Championship', 'Spain Segunda', 'Italy Serie B',
    'Germany Bundesliga II', 'France Ligue 2', 'Belgium First Division A',
    'Türkiye Super Lig', 'Turkey Super Lig', 'Austria Bundesliga',
    'Switzerland Super League', 'Scotland Premiership', 'Denmark Superligaen',
    'Sweden Allsvenskan', 'Finland Veikkausliiga', 'Poland Ekstraklasa',
    'Czechia First League', 'Greece Super League', 'Romania Liga I',
    'Croatia HNL', 'Serbia Super Liga', 'Bulgaria First League',
    'Japan J-League', 'South Korea K League 1', 'Saudi Arabia Pro League',
    'Australia A-League', 'Netherlands Eerste Divisie', 'Portugal Segunda Liga',
)}
EXCLUDED = re.compile(r'esoccer|e-?soccer|\bsrl\b|virtual|simulat', re.I)


def league_priority(event):
    label = norm(event.get('league'))
    return 0 if label in PRIMARY else 1 if label in SECONDARY else 2


def eligible_events(events, now_epoch, *, hours=None, days=5, min_lead=0):
    unique = {}
    end = now_epoch + (hours * 3600 if hours is not None else days * 86400)
    for event in events:
        if not isinstance(event, dict) or EXCLUDED.search(str(event.get('league') or '')):
            continue
        try:
            kickoff = float(event.get('time') or 0)
        except (ValueError, TypeError):
            continue
        ident = event.get('fi')
        if ident in (None, '') or not event.get('home') or not event.get('away'):
            continue
        if now_epoch + min_lead < kickoff <= end:
            unique[str(ident)] = event
    return sorted(unique.values(), key=lambda e: (league_priority(e), float(e['time']), str(e['fi'])))


def merge_inventory(previous, current, now_epoch):
    # Current discovery wins; retain future FIs from unvisited pages for rotation.
    merged = {str(e['fi']): e for e in eligible_events(previous, now_epoch)}
    for event in current:
        if isinstance(event, dict) and event.get('fi') not in (None, ''):
            merged.pop(str(event['fi']), None)
    merged.update({str(e['fi']): e for e in eligible_events(current, now_epoch)})
    return eligible_events(list(merged.values()), now_epoch)


def quote_age_hours(record, now_epoch):
    """Source observation age, never a snapshot/processing-clock fallback."""
    from history_quality import ensure_aware, parse_ts
    observed = ensure_aware(parse_ts(record.get('captured_at')))
    if observed is None or observed.timestamp() > now_epoch + 60:
        return None
    return max(0, (now_epoch - observed.timestamp()) / 3600)


def retained_quotes(previous, inventory, selected_ids, now_epoch, max_age_h=12):
    """Keep unvisited future quotes, with the ORIGINAL observation timestamp.

    Selected IDs are tombstones even when upstream validly returns no markets.
    Relisted/corrected kickoffs cannot inherit the old event's observations.
    The board independently suppresses value flags after 2h and omits after 12h.
    """
    from capture_common import _start_to_utc
    current = {str(e['fi']): e for e in eligible_events(inventory, now_epoch)}
    seen, kept = set(), []
    for record in previous:
        if not isinstance(record, dict):
            continue
        if record.get('parser_contract') != 2:
            continue  # legacy corners may be three-way; only retain validated parser generation
        ident = str(record.get('event_id') or '')
        if ident in seen or ident in selected_ids or ident not in current:
            continue
        if record.get('name') != f"{current[ident]['home']} - {current[ident]['away']}":
            continue  # corrected participants cannot inherit the old quotation
        start = _start_to_utc(record.get('start'))
        age = quote_age_hours(record, now_epoch)
        if start is None or start.timestamp() != float(current[ident]['time']):
            continue
        if age is None or age > max_age_h or not (record.get('mercados') or record.get('mercados_time')):
            continue
        kept.append(dict(record, retained_from_previous=True))
        seen.add(ident)
    return kept
