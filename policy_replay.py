"""Pre-deadline market snapshots and explicitly virtual policy comparisons."""
import datetime
import glob
import json
import math
import os
import re

from settlement import settle


def valid_pick(pick):
    return bool(re.fullmatch(r'[1-6]-[1-6]-[1-6]', str(pick))) and len(set(str(pick).split('-'))) == 3


def numbers(mapping, probability=False):
    out = {}
    for pick, value in mapping.items():
        try:
            value = float(value)
        except (ValueError, TypeError):
            continue
        if valid_pick(pick) and math.isfinite(value) and value > 0 and (not probability or value <= 1):
            out[pick] = value
    return out


def select(probabilities, odds, min_prob=.12, ev1=1., ev2=.8, max_bets=2):
    p, odds = numbers(probabilities, True), numbers(odds)
    ranked = sorted([(k, q, odds[k]) for k, q in p.items() if q >= .01 and k in odds], key=lambda x: -x[1])
    if not ranked or ranked[0][1] < min_prob or ranked[0][1]*ranked[0][2] < ev1:
        return []
    return [ranked[0][0]]+[k for k, q, o in ranked[1:max_bets] if q*o >= ev2]


def capture(logs, hd, jcd, rno, deadline, started, observed, probabilities, odds, model_ids, settings, features_observed_at=None):
    p, o = numbers(probabilities, True), numbers(odds)
    full = len(p) == 120 and len(o) == 120 and abs(sum(p.values())-1) < 1e-6
    verified = full and started <= observed and observed <= deadline-datetime.timedelta(minutes=settings.get('safe_min', 2))
    row = dict(hd=str(hd), jcd=int(jcd), rno=int(rno), deadline=deadline.isoformat(),
               odds_started_at=started.isoformat(), observed_at=observed.isoformat(),
               features_observed_at=features_observed_at, model_ids=model_ids,
               asof_verified=bool(verified), complete_market=bool(full),
               probabilities=p, odds=o, policy=settings)
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, f'market_{hd}.jsonl'), 'a', encoding='utf8') as f:
        f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
    return row


def report(logs, results_for, until):
    import pandas as pd
    snapshots = []
    for path in sorted(glob.glob(os.path.join(logs, 'market_*.jsonl'))):
        with open(path, encoding='utf8') as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if row.get('asof_verified') and str(row['hd']) <= until:
                        snapshots.append(row)
                except (ValueError, KeyError):
                    continue
    # Repeated retrievals must not select whichever later snapshot won.
    first = {}
    for row in sorted(snapshots, key=lambda r: r['observed_at']):
        first.setdefault((row['hd'], row['jcd'], row['rno']), row)
    results, records = {}, []
    for (hd, jcd, rno), row in first.items():
        if hd not in results:
            table = results_for(hd)
            results[hd] = {(int(r['jcd']), int(r['rno'])): r for r in table.to_dict('records')} if table is not None else {}
        outcome = results[hd].get((jcd, rno), {})
        settings = row.get('policy', {})
        base = dict(min_prob=settings.get('min_prob', .12), ev1=settings.get('ev1', 1.), ev2=settings.get('ev2', .8), max_bets=settings.get('max_bets', 2))
        policies = {'current_two': base, 'first_only': dict(base, max_bets=1), 'second_ev1': dict(base, ev2=1.)}
        for name, policy in policies.items():
            picks = select(row['probabilities'], row['odds'], **policy)
            settled = [settle(pick, outcome) for pick in picks]
            resolved = [x for x in settled if x[0] != 'pending']
            records.append(dict(hd=hd, jcd=jcd, rno=rno, policy=name, picks=' '.join(picks),
                                bets=len(picks), pending=len(settled)-len(resolved),
                                hits=sum(x[1] for x in resolved), refunds=sum(x[0]=='refunded' for x in resolved),
                                investment=100*len(resolved), returned=sum(x[2] for x in resolved)))
    if not records:
        return None
    detail = pd.DataFrame(records)
    detail.to_csv(os.path.join(logs, 'policy_replay.csv'), index=False)
    summary = detail.groupby('policy')[['bets', 'pending', 'hits', 'refunds', 'investment', 'returned']].sum().reset_index()
    summary['roi_pct'] = summary.returned / summary.investment.where(summary.investment > 0)*100
    summary.to_csv(os.path.join(logs, 'policy_replay_summary.csv'), index=False)
    return summary
