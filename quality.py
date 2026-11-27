"""All judged picks: calibration, sampling uncertainty, and source coverage."""
import glob
import os
import numpy as np
import pandas as pd


def report(logs, results_for, until):
    records = []
    for path in sorted(glob.glob(os.path.join(logs, 'notify_*.csv'))):
        day = os.path.basename(path)[7:15]
        if day > until:
            continue
        t = pd.read_csv(path, dtype=str)
        t = t[t['hd'] != 'hd'].drop_duplicates('notify_id', keep='last')
        rc = results_for(day)
        if rc is None or not len(rc):
            continue
        result = {(int(r.jcd), int(r.rno)): r for r in rc.itertuples()}
        for row in t.itertuples():
            r = result.get((int(row.jcd), int(row.rno)))
            if r is None or not isinstance(r.tri, str):
                continue
            payouts = getattr(r, 'tri_payouts', {})
            valid = set(payouts) if isinstance(payouts, dict) and payouts else {r.tri}
            picks = str(row.picks).split()
            probs = str(row.probs).split()
            for rank, (pick, prob) in enumerate(zip(picks, probs), 1):
                try:
                    p = float(prob)
                except ValueError:
                    continue
                if not 0 < p < 1:
                    continue
                refunds = set(str(getattr(r, 'refund_boats', '')))
                if refunds.intersection(pick.split('-')):
                    continue
                y = int(pick in valid)
                records.append(dict(day=day, venue=row.venue, rank=rank,
                                    status=row.status, prob=p, hit=y,
                                    brier=(p-y)**2,
                                    logloss=-(y*np.log(p)+(1-y)*np.log1p(-p))))
    if not records:
        return
    d = pd.DataFrame(records)
    d['band'] = pd.cut(d.prob, [0, .05, .08, .10, .12, .15, .20, 1], right=False)
    g = d.groupby(['rank', 'band'], observed=True)
    out = g.agg(n=('hit', 'size'), predicted=('prob', 'mean'), actual=('hit', 'mean'),
                brier=('brier', 'mean'), logloss=('logloss', 'mean')).reset_index()
    # Wilson 95% interval: small samples must not look like proven accuracy.
    n, p, z = out.n, out.actual, 1.96
    center = (p+z*z/(2*n))/(1+z*z/n)
    width = z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    out['hit_low95'], out['hit_high95'] = center-width, center+width
    out.to_csv(os.path.join(logs, 'calibration_all.csv'), index=False)
    d.groupby(['venue', 'rank']).agg(n=('hit', 'size'), predicted=('prob', 'mean'),
                                    actual=('hit', 'mean')).reset_index().to_csv(
        os.path.join(logs, 'calibration_venue.csv'), index=False)
