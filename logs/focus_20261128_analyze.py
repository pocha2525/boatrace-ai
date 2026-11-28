"""Read saved forecasts only; no fetching odds, fitting models or sending notices."""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

AUDIT_VERSION = 'focus-allocation-v1'


def read(repo, ref, path):
    return subprocess.check_output(['git', '-C', str(repo), 'show', f'{ref}:{path}']).decode()


def allocate(picks, odds, policy):
    if not picks:
        return {}
    if policy == 'equal100':
        return dict.fromkeys(picks, 100)
    budget = 100  # 100 units of 100 yen, exactly 10,000 yen per selected race.
    if policy == 'flat10000':
        q, rem = divmod(budget, len(picks))
        return {p: (q + (i < rem))*100 for i, p in enumerate(picks)}
    values = [round(float(odds[p])*10) for p in picks]
    if any(v <= 0 for v in values):
        return None
    # Enumerate possible minimum payouts; feasibility uses integer arithmetic.
    best = max(t for v in values for u in range(1, budget-len(picks)+2)
               for t in [v*u] if sum((t+w-1)//w for w in values) <= budget)
    units = [(best+v-1)//v for v in values]
    while sum(units) < budget:
        i = min(range(len(picks)), key=lambda i: (units[i]*values[i], i))
        units[i] += 1
    return {p: u*100 for p, u in zip(picks, units)}


def ranked(row, source):
    p = row['probabilities'] if source == 'baseline' else row['shadow']['probabilities']
    return sorted(p, key=lambda k: -p[k])


def select(row, source, count, threshold=None, ev2=.8):
    if source == 'rules':
        return row['reasoned']['picks'][:count]
    p = row['probabilities'] if source == 'baseline' else row['shadow']['probabilities']
    ranking = ranked(row, source)
    if threshold is None:
        return ranking[:count]
    ranking = [k for k in ranking if p[k] >= .01 and row['odds'].get(k, 0) > 0]
    if not ranking or p[ranking[0]] < threshold or p[ranking[0]]*row['odds'][ranking[0]] < 1:
        return []
    return ranking[:1]+[k for k in ranking[1:count] if p[k]*row['odds'][k] >= ev2]


def settle(amounts, result):
    if not result or result.get('race_status') not in ('settled', 'cancelled'):
        return None
    refunds = set(result.get('refund_boats', ''))
    pays = json.loads(result.get('tri_payouts') or '{}')
    returned, hits, refunded = 0, 0, 0
    for pick, amount in amounts.items():
        if result['race_status'] == 'cancelled' or refunds.intersection(pick.split('-')):
            returned += amount; refunded += amount
        else:
            returned += int(pays.get(pick, 0))*amount//100
            hits += int(pick in pays)
    return returned, hits, refunded


def summarize(records):
    done = [r for r in records if not r['pending']]
    invested = sum(r['invested'] for r in done)
    returned = sum(r['returned'] for r in done)
    equity = peak = mdd = 0
    for row in done:
        equity += row['returned']-row['invested']
        peak = max(peak, equity); mdd = max(mdd, peak-equity)
    largest = max((r for r in done if r['hits']), key=lambda r: r['returned'], default=None)
    without = [r for r in done if r is not largest]
    wi = sum(r['invested'] for r in without); wr = sum(r['returned'] for r in without)
    days = sorted({r['day'] for r in done})
    interval = None
    if len(days) >= 2 and invested:
        daily = [(sum(r['invested'] for r in done if r['day'] == day),
                  sum(r['returned'] for r in done if r['day'] == day)) for day in days]
        rng = random.Random(20261128); samples = []
        for _ in range(3000):
            draws = rng.choices(daily, k=len(daily)); si = sum(r[0] for r in draws)
            if si: samples.append(sum(r[1] for r in draws)/si*100)
        samples.sort()
        if len(samples) >= 2970:
            interval = [samples[int(.025*len(samples))], samples[min(len(samples)-1, int(.975*len(samples)))]]
    return dict(selected=len(records), settled=len(done), pending=len(records)-len(done),
        hits=sum(bool(r['hits']) for r in done),
        hit_rate_pct=sum(bool(r['hits']) for r in done)/len(done)*100 if done else None,
        investment=invested, returned=returned, profit=returned-invested,
        roi_pct=returned/invested*100 if invested else None,
        day_ci95=interval, days=len(days), ci_note='One day cannot estimate day-to-day variation' if len(days)<2 else 'Few days remain unreliable',
        largest_hit=largest['race'] if largest else None,
        largest_return=largest['returned'] if largest else 0,
        roi_without_largest_hit=wr/wi*100 if wi else None,
        max_drawdown=mdd if invested else None, equity_peak=peak if invested else None)


def main():
    if sys.argv[1:]==['--self-test']:
        for a,b in [(23,41),(10,80),(999,17)]:
            odds={'1-2-3':a/10,'1-3-2':b/10}
            amounts=allocate(list(odds),odds,'dutch10000')
            achieved=min(amounts[p]//100*round(odds[p]*10) for p in odds)
            optimum=max(min(u*a,(100-u)*b) for u in range(1,100))
            assert achieved==optimum and sum(amounts.values())==10000
        picks=['1-2-3','1-3-2','1-2-4','1-4-2'];odds=dict(zip(picks,[4.6,13.4,20.1,71.2]))
        amounts=allocate(picks,odds,'dutch10000')
        achieved=min(amounts[p]//100*round(odds[p]*10) for p in picks)
        assert sum(amounts.values())==10000
        assert sum((achieved+round(odds[p]*10))//round(odds[p]*10) for p in picks)>100
        assert settle(amounts,None) is None
        assert settle({'1-2-3':500},{'race_status':'settled','tri_payouts':'{"1-2-3":970}','refund_boats':''})==(4850,1,0)
        assert settle({'1-2-3':500},{'race_status':'settled','tri_payouts':'{}','refund_boats':'2'})==(500,0,500)
        print('Allocation optimum, budget, settlement, refund and pending checks passed')
        return
    ap = argparse.ArgumentParser(); ap.add_argument('--repo', required=True); ap.add_argument('--ref', required=True)
    ap.add_argument('--out', required=True); ap.add_argument('--previous')
    ap.add_argument('--full-grid', action='store_true')
    ap.add_argument('--focus-start', default='2026-11-28T13:13:27+09:00')
    ap.add_argument('--focus-end', default='2026-11-28T15:13:27+09:00')
    args = ap.parse_args(); repo = Path(args.repo); out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    market = read(repo,args.ref,'logs/market_20261128.jsonl')
    raw = [json.loads(s) for s in market.splitlines() if s.strip()]
    results_text=read(repo,args.ref,'logs/results_20261128.csv')
    outcomes = {f'{r["hd"]}-{int(r["jcd"])}-{int(r["rno"])}':r for r in csv.DictReader(io.StringIO(results_text))}
    inputs_id=hashlib.sha256((market+'\n'+results_text).encode()).hexdigest()
    outcome_fingerprints={key:hashlib.sha256(json.dumps({k:r.get(k) for k in ('race_status','tri','tri_pay','tri_payouts','refund_boats')},sort_keys=True).encode()).hexdigest() for key,r in outcomes.items()}
    manifest = json.loads(read(repo,args.ref,'model/shadow/manifest.json'))
    rule_hash = hashlib.sha256(read(repo,args.ref,'reasoned_forecast.py').encode()).hexdigest()
    first = {}
    for row in sorted(raw,key=lambda r:r['observed_at']):
        if row.get('asof_verified') and row.get('complete_market'):
            first.setdefault(f'{row["hd"]}-{row["jcd"]}-{row["rno"]}', row)
    for key,row in first.items():
        row['_key']=key
        if row['model_ids'] != manifest['baseline_ids'] or row.get('shadow',{}).get('manifest',{}).get('candidate_ids') != manifest['candidate_ids']:
            raise ValueError('Frozen model IDs changed')
        for p in (row['probabilities'],row['shadow']['probabilities']):
            if len(p)!=120 or abs(sum(p.values())-1)>1e-6 or not all(math.isfinite(v) and 0<v<=1 for v in p.values()):
                raise ValueError('Incomplete probabilities')
        if len(row['odds'])!=120 or not all(math.isfinite(v) and v>0 for v in row['odds'].values()):
            raise ValueError('Incomplete market')
        rule=row.get('reasoned')
        row['_rule_ok']=False
        if rule and rule.get('eligible') and rule.get('rules_id')==rule_hash:
            times=[dt.datetime.fromisoformat(x) for x in [row['features_observed_at'],rule['observed_at'],row['odds_started_at'],row['observed_at'],row['deadline']]]
            picks=rule['picks']
            row['_rule_ok']=(all(t.tzinfo is not None for t in times) and times==sorted(times)
                and times[3]<=times[4]-dt.timedelta(minutes=2) and len(set(picks))==4
                and all(re.fullmatch(r'[1-6]-[1-6]-[1-6]',p) and len(set(p.split('-')))==3 for p in picks))
    allrows=list(first.values()); matched=[r for r in allrows if r['_rule_ok']]
    fingerprints={r['_key']:hashlib.sha256(json.dumps({k:v for k,v in r.items() if not k.startswith('_')},sort_keys=True).encode()).hexdigest() for r in allrows}
    previous_keys=set(); previous_records={}; previous_names=set()
    if args.previous:
        prev=Path(args.previous)
        meta=json.loads((prev/'summary.json').read_text())
        if meta['metadata'].get('audit_version') != AUDIT_VERSION:
            raise ValueError('Analysis policy changed; do not mix experiments')
        if meta['metadata'].get('focus_start')!=args.focus_start or meta['metadata'].get('focus_end')!=args.focus_end:
            raise ValueError('Focus window changed')
        if meta['metadata'].get('rule_hash')!=rule_hash:
            raise ValueError('Forecast rules changed during the fixed analysis')
        for key,value in meta['metadata']['outcome_fingerprints'].items():
            if outcome_fingerprints.get(key)!=value:
                raise ValueError('A settled outcome was revised; reconcile explicitly')
        if meta['metadata'].get('inputs_id')==inputs_id and meta['metadata'].get('full_grid')==args.full_grid:
            if prev.resolve()!=out.resolve():
                for name in ('summary.json','detail.csv'):shutil.copyfile(prev/name,out/name)
            print('No new input data; reused the previous analysis without recomputing')
            return
        for key,value in meta['metadata']['fingerprints'].items():
            if fingerprints.get(key) != value:
                raise ValueError('An earlier first market snapshot was changed or removed')
        previous_keys=set(meta['metadata']['fingerprints'])
        previous_names={r['profile'] for r in meta['profiles']}
        for old in csv.DictReader((prev/'detail.csv').open()):
            old['pending']=old['pending']=='True'
            for k in ('invested','returned','hits','refund'):old[k]=int(old[k])
            previous_records[(old['profile'],old['race'])]=old
    modes=[('matched_full',matched),('all_gated',allrows),('focus_matched',[r for r in matched if args.focus_start<=r['observed_at']<=args.focus_end]),('focus_gated',[r for r in allrows if args.focus_start<=r['observed_at']<=args.focus_end])]
    profiles=[]; details=[]
    for scope, rows in modes:
        for source in (('baseline','candidate') if 'gated' in scope else ('baseline','candidate','rules')):
            for count in (1,2,4):
                thresholds=([v/200 for v in range(4,61)] if args.full_grid else [.11,.12,.15]) if 'gated' in scope else [None]
                for threshold in thresholds:
                    evs=(.8,1.) if 'gated' in scope and count==2 else (1.,)
                    for ev2 in evs:
                        for allocation in (('equal100','flat10000','dutch10000') if threshold is None and count==4 else ('equal100',)):
                            name=f'{scope}/{source}/{count}/p{threshold}/ev2={ev2}/{allocation}'
                            records=[]
                            for row in rows:
                                old=previous_records.get((name,row['_key']))
                                if old and not old['pending']:
                                    records.append(old);continue
                                if old:
                                    amounts=json.loads(old['amounts']);picks=old['picks'].split()
                                elif row['_key'] in previous_keys and name in previous_names:
                                    continue  # Previously selected no tickets; do not rerun the old selection.
                                else:
                                    picks=select(row,source,count,threshold,ev2)
                                    if not picks:continue
                                    amounts=allocate(picks,row['odds'],allocation)
                                if amounts is None:raise ValueError('Missing odds for allocation')
                                result=settle(amounts,outcomes.get(row['_key']))
                                rec=dict(profile=name,race=row['_key'],day=row['hd'],observed_at=row['observed_at'],
                                    picks=' '.join(picks),amounts=json.dumps(amounts,separators=(',',':')),
                                    pending=result is None,invested=sum(amounts.values()) if result else 0,
                                    returned=result[0] if result else 0,hits=result[1] if result else 0,
                                    refund=result[2] if result else 0)
                                records.append(rec)
                            profiles.append(dict(profile=name,**summarize(records)));details.extend(records)
    metadata=dict(ref=args.ref,created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        audit_version=AUDIT_VERSION,fingerprints=fingerprints,full_grid=args.full_grid,
        inputs_id=inputs_id,outcome_fingerprints=outcome_fingerprints,
        focus_start=args.focus_start,focus_end=args.focus_end,market_rows=len(raw),unique_verified=len(allrows),
        latest_observed=allrows[-1]['observed_at'] if allrows else None,results=len(outcomes),
        matched_rules=len(matched),rule_hash=rule_hash,manifest=manifest,
        status='Exploratory allocation sensitivity; no changes to production or forecasts',
        evidence='All choices are recorded pre-odds picks; allocation policies are defined after seeing earlier outcomes',
        statistics='Pending outcomes are excluded from settled investment; largest-hit removal drops one whole race; drawdown uses forecast-observation order')
    (out/'summary.json').write_text(json.dumps(dict(metadata=metadata,profiles=profiles),ensure_ascii=False,indent=2))
    with (out/'detail.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(details[0]) if details else ['profile'],lineterminator='\n');w.writeheader();w.writerows(details)
    print(json.dumps(dict(ref=args.ref,market_rows=len(raw),matched_rules=len(matched),results=len(outcomes),profiles=len(profiles),output=str(out)),ensure_ascii=False))


if __name__=='__main__':
    main()
