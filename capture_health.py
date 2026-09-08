"""Shared capture status semantics. Does not relax capture/deploy eligibility."""
from datetime import datetime, timezone, timedelta
import json
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

def state(status, house=None, now=None, max_age_minutes=90):
    """A guard-protected feed is not a failed capture, nor proof of current odds."""
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
    return 'ok' if status.get('ok') else 'failed'

def load_status(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (ValueError,OSError):return {}

def board_capture(status_dir, jogos, now=None):
    now=now or datetime.now(timezone.utc)
    cap={'casas_ok':[],'casas_fail':[],'casas_stale':[], 'casas_protected':[],
         'casas_disabled':[NAMES[h] for h in DISABLED_HOUSES],'source_states':{}}
    for house in ACTIVE_HOUSES:
        st=load_status(Path(status_dir)/(house+'.json')); s=state(st,house,now)
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
                a=agg.setdefault(NAMES[house],{'ok':0,'total':0,'protected':0,'legacy_unknown':0})
                if st.get('source_state')=='protected_feed':a['protected']+=1;continue
                a['total']+=1;a['ok']+=int(bool(st.get('ok')))
                if not st.get('source_state') and not st.get('ok'):a['legacy_unknown']+=1
    if agg:cap['hist7']=agg
    return cap
