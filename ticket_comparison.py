"""Re-score saved, preselected predictions without fitting or changing thresholds."""
import csv
import json
import math
from pathlib import Path


def metrics(rows):
    selected = []
    for row in rows:
        if float(row['top1_probability']) < .12:
            continue
        pay = float(row['tri_pay'])
        picks = [row['pick1'], row['pick2']]
        if not math.isfinite(pay) or pay <= 0 or len(set(picks)) != 2:
            raise ValueError('Saved evaluation rows have invalid settlement data')
        selected.append((row['tri'] == picks[0], row['tri'] in picks, pay))
    n = len(selected)
    h1 = sum(x[0] for x in selected)
    h2 = sum(x[1] for x in selected)
    ret1 = sum(x[2] for x in selected if x[0])
    ret2 = sum(x[2] for x in selected if x[1])
    return dict(selected12_n=n, selected12_hits=h1, selected12_two_hits=h2,
                selected12_hit_pct=h1/n*100 if n else None,
                selected12_roi_pct=ret1/n if n else None,
                selected12_two_hit_pct=h2/n*100 if n else None,
                selected12_two_roi_pct=ret2/(2*n) if n else None,
                one_investment=100*n, one_returned=ret1, one_profit=ret1-100*n,
                two_investment=200*n, two_returned=ret2, two_profit=ret2-200*n)


def enrich(summary, path):
    races = Path(path).with_name('races.csv')
    if not races.exists():
        return summary
    with races.open(encoding='utf8', newline='') as f:
        reader = csv.DictReader(f)
        # Legacy experiments without saved winning order cannot be re-scored.
        if 'tri' not in (reader.fieldnames or []):
            return summary
        arms = {}
        for row in reader:
            arms.setdefault(row['arm'], []).append(row)
    for name, rows in arms.items():
        if name not in summary['metrics']:
            raise ValueError('Saved predictions do not match experiment summary')
        stat = metrics(rows)
        prior = summary['metrics'][name]
        for key in ('selected12_n', 'selected12_hit_pct', 'selected12_roi_pct'):
            if key in prior and prior[key] is not None and abs(prior[key]-stat[key]) > 1e-7:
                raise ValueError('Saved predictions do not match previous score')
        prior.update(stat)
    return summary


def export(summaries, target):
    records = []
    for summary in summaries:
        for arm, values in summary['metrics'].items():
            if 'selected12_two_roi_pct' not in values:
                continue
            row = dict(holdout_from=summary['holdout_from'], holdout_until=summary['holdout_until'],
                       input_mode=summary.get('input_mode', 'observed'), arm=arm,
                       condition='top1_probability>=0.12; equal 100 yen per ticket; no EV filter')
            row.update({k: v for k, v in values.items() if k.startswith('selected12_') or k.startswith(('one_', 'two_'))})
            records.append(row)
    if records:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('w', encoding='utf8', newline='') as f:
            fields = list(dict.fromkeys(k for r in records for k in r))
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    return records
