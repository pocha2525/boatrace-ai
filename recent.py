"""Experimental recent-form features, excluding the entire prediction day.

Historical actual courses are used only for previous results. Query courses
come from exhibition observations (or an explicitly labelled boat assumption).
"""
import numpy as np
import pandas as pd

FEATURES = ['r90_win', 'r90_top2', 'r90_top3', 'r90_st', 'r90_n',
            'rc180_win', 'rc180_top2', 'rc180_top3', 'rc180_n',
            'vc90_win', 'vc90_top2', 'vc90_top3', 'r90_win_d', 'rc180_win_d']


def window(history, query, keys, days, prefix, with_st=False):
    h = history.copy(deep=False)
    values = ['win', 'top2', 'top3'] + (['st_ok'] if with_st else [])
    daily = h.groupby(keys+['date'])[values].agg(['sum', 'count'])
    daily.columns = [f'{a}_{b}' for a, b in daily.columns]
    daily = daily.reset_index().sort_values('date')
    cols = [c for c in daily if c not in keys+['date']]
    daily[cols] = daily.groupby(keys)[cols].cumsum()
    daily['_ord'] = pd.to_datetime(daily.date.astype(str), format='%Y%m%d').map(pd.Timestamp.toordinal).astype('int64')
    q = query[keys+['date']].copy()
    q['_ord'] = pd.to_datetime(q.date.astype(int).astype(str), format='%Y%m%d').map(pd.Timestamp.toordinal).astype('int64')
    q['_row'] = np.arange(len(q))
    for k in keys:
        daily[k], q[k] = daily[k].astype('int64'), q[k].astype('int64')
    def totals(offset):
        left = q.drop(columns='date').copy()
        left['_ord'] -= offset
        joined = pd.merge_asof(left.sort_values('_ord'), daily[keys+['_ord']+cols],
                               on='_ord', by=keys, direction='backward', allow_exact_matches=False)
        return joined.sort_values('_row')[cols].fillna(0).reset_index(drop=True)
    counts = totals(0)-totals(days)
    out = pd.DataFrame(index=np.arange(len(query)))
    out[prefix+'_n'] = counts['win_count'].to_numpy()
    for col in values:
        name = 'st' if col == 'st_ok' else col
        out[prefix+'_'+name] = counts[col+'_sum']/counts[col+'_count'].where(counts[col+'_count'] >= 5)
    return out


def attach(history, query):
    out = query.copy()
    for keys, days, prefix, st in [(['racer'], 90, 'r90', True),
                                  (['racer', 'course'], 180, 'rc180', False),
                                  (['venue', 'course'], 90, 'vc90', False)]:
        calculated = window(history, query, keys, days, prefix, st)
        for name in calculated:
            out[name] = calculated[name].to_numpy()
    out['r90_win_d'] = out.r90_win-out.r_win
    out['rc180_win_d'] = out.rc180_win-out.rc_win
    return out
