"""Shared capture status semantics. Does not relax capture/deploy eligibility."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path

BRT = timezone(timedelta(hours=-3))
ACTIVE_HOUSES = ('betano','superbet','estrelabet','7k','pinnacle','bet365','sportingbet')
DISABLED_HOUSES = ('betfast',)
NAMES = {'betano':'Betano','superbet':'Superbet','estrelabet':'EstrelaBet','7k':'7k',
         'pinnacle':'Pinnacle','bet365':'bet365','sportingbet':'Sportingbet','betfast':'Betfast','sofa':'SofaScore'}

def parse_time(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=BRT)
    except (ValueError,TypeError): return None

def local_feed_guard_min():
    """Mesma régua do capture_common._local_feed_guard_reason (env LOCAL_FEED_GUARD_MIN, 75)."""
    try:
        return float(os.environ.get("LOCAL_FEED_GUARD_MIN", "75"))
    except ValueError:
        return 75.0

def local_feed_age_min(house, odds_dir, now=None):
    """Idade (min) do full LOCAL da casa (captured_by=local), ou None (22/09/2026, A13a).

    É o FEED EFETIVO: desde 20/09 a Pinnacle devolve 429 pro IP do Actions/proxy, mas o
    feeder do Windows mantém o full dela fresco (60 pushes em 34 h, maior intervalo
    37 min). Ponteiro sem alvo, vazio ou sem carimbo não conta como feed.
    """
    try:
        meta=json.loads((Path(odds_dir)/f"{house}_latest_full.json").read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return None
    if not isinstance(meta,dict) or meta.get('captured_by')!='local' or not meta.get('file'):
        return None
    try:
        if int(meta.get('n') or 0)<=0 or not (Path(odds_dir)/str(meta['file'])).is_file():
            return None
    except (TypeError,ValueError):
        return None
    at=parse_time(meta.get('at'))
    if at is None:
        return None
    return ((now or datetime.now(timezone.utc))-at).total_seconds()/60

def state(status, house=None, now=None, max_age_minutes=90, local_feed_min=None):
    """A guard-protected feed is not a failed capture, nor proof of current odds.

    22/09/2026 (A13a): a tentativa do Actions que falha (429) enquanto o feed LOCAL
    da casa está fresco (< LOCAL_FEED_GUARD_MIN) é 'protected_feed', não 'failed' —
    antes só virava protected quando o Actions pegava n>0 e a guarda barrava, e o
    429 (n=0) gerava o aviso falso "Captura parcial: Pinnacle". Feeder morto (feed
    local passou da guarda) volta a 'failed': o teste de fronteira 74/76 trava isso.
    Nada disso conta como 'ok' — a elegibilidade de deploy não afrouxa.
    """
    if house in DISABLED_HOUSES:return 'disabled'
    if not status:return 'unknown'
    now=now or datetime.now(timezone.utc)
    at=parse_time(status.get('ts_utc') or status.get('ts_brt'))
    if at is None:return 'unknown'
    age=(now-at).total_seconds()/60
    if age < -5:return 'unknown'
    if age > max_age_minutes:return 'stale'
    if status.get('error_class')=='Pending':return 'running'
    reasons=status.get('promotion_blocked') or []
    if isinstance(reasons,str):reasons=[reasons]
    msg='; '.join(map(str,reasons))+' '+str(status.get('error') or '')
    if 'feed local fresco mais rico' in msg:
        return 'protected_feed'
    local_fresco=local_feed_min is not None and 0<=local_feed_min<local_feed_guard_min()
    if status.get('skipped_reason')=='local_feed_fresh':
        # tentativa do Actions PULADA de propósito (run_capture): nunca é 'ok' desta rodada
        return 'protected_feed' if (local_feed_min is None or local_fresco) else 'failed'
    if status.get('ok'):return 'ok'
    return 'protected_feed' if local_fresco else 'failed'

def load_status(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (ValueError,OSError):return {}

def board_capture(status_dir, jogos, now=None):
    now=now or datetime.now(timezone.utc)
    cap={'casas_ok':[],'casas_fail':[],'casas_stale':[], 'casas_protected':[],
         'casas_disabled':[NAMES[h] for h in DISABLED_HOUSES],'source_states':{}}
    odds_dir=Path(status_dir).parent   # data/odds/_status → data/odds (ponteiros *_latest_full)
    for house in ACTIVE_HOUSES:
        st=load_status(Path(status_dir)/(house+'.json'))
        s=state(st,house,now,local_feed_min=local_feed_age_min(house,odds_dir,now))
        name=NAMES[house];cap['source_states'][name]=s
        if s=='ok':cap['casas_ok'].append(name)
        elif s=='protected_feed':cap['casas_protected'].append(name)
        elif s=='stale':cap['casas_stale'].append(name)
        else:cap['casas_fail'].append({'casa':name,'error':str(st.get('error') or {'unknown':'sem status recente verificável','running':'captura em andamento'}.get(s,'captura falhou'))[:120], 'error_class':st.get('error_class'),'source_state':s})
    cap['casas_stale']=sorted(set(cap['casas_stale']) | {h for j in jogos for h in j.get('stale_casas',[]) if h not in cap['casas_disabled']})
    history=Path(status_dir)/'history.jsonl';agg={}
    if history.exists():
        for ln in history.read_text(encoding='utf-8').splitlines():
            try:r=json.loads(ln)
            except ValueError:continue
            ts=parse_time(r.get('ts'))
            if ts is None or ts < now-timedelta(days=7):continue
            for house,st in (r.get('casas') or {}).items():
                if house not in ACTIVE_HOUSES:continue
                if st.get('attempted') is False:continue
                a=agg.setdefault(NAMES[house],{'ok':0,'total':0,'protected':0,'legacy_unknown':0})
                if st.get('source_state')=='protected_feed':a['protected']+=1;continue
                a['total']+=1;a['ok']+=int(bool(st.get('ok')))
                if not st.get('source_state') and not st.get('ok'):a['legacy_unknown']+=1
    if agg:cap['hist7']=agg
    return cap
