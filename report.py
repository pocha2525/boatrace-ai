"""通知した買い目の成績（的中率・回収率）を集計して通知する。"""
import os
import glob
import datetime

import pandas as pd

from common import LOGS, now_jst, download_k, read_k, notify


def results_for(hd):
    d = datetime.datetime.strptime(hd, '%Y%m%d').date()
    path = download_k(d)
    if path is None:
        return None
    _, races = read_k(path)
    rc = pd.DataFrame(races)
    return rc.rename(columns={'venue': 'jcd', 'race': 'rno'})


def score(hd):
    log = pd.read_csv(os.path.join(LOGS, f'{hd}.csv'), dtype={'pick': str})
    rc = results_for(hd)
    if rc is None:
        return None
    bets = log[log['pick'].fillna('') != ''].copy()
    bets = bets.merge(rc[['jcd', 'rno', 'tri', 'tri_pay']],
                      on=['jcd', 'rno'], how='left')
    bets = bets[bets['tri'].notna()]  # 中止・返還は除く
    bets['hit'] = bets['pick'] == bets['tri']
    bets['ret'] = bets['tri_pay'].where(bets['hit'], 0)
    return {
        'races': log[['jcd', 'rno']].drop_duplicates().shape[0],
        'bet_races': bets[['jcd', 'rno']].drop_duplicates().shape[0],
        'bets': len(bets),
        'hits': int(bets['hit'].sum()),
        'ret': float(bets['ret'].sum()),
    }


def fmt(s):
    roi = s['ret'] / (s['bets'] * 100) * 100 if s['bets'] else 0
    return (f'予想{s["races"]}R / 買い目あり{s["bet_races"]}R / '
            f'{s["bets"]}点 / 的中{s["hits"]} / 回収率{roi:.1f}%')


def main():
    hd = os.environ.get('HD') or \
        f'{now_jst().date() - datetime.timedelta(days=1):%Y%m%d}'
    total = {'races': 0, 'bet_races': 0, 'bets': 0, 'hits': 0, 'ret': 0.0}
    day = None
    for path in sorted(glob.glob(os.path.join(LOGS, '*.csv'))):
        h = os.path.basename(path)[:8]
        if h > hd:
            continue
        s = score(h)
        if s is None:
            continue
        if h == hd:
            day = s
        for k in total:
            total[k] += s[k]
    if day is None and total['races'] == 0:
        print('集計できる記録がまだありません')
        return
    lines = []
    if day:
        lines.append(f'{hd[4:6]}/{hd[6:]}: ' + fmt(day))
    lines.append('累計: ' + fmt(total))
    lines.append('※1点100円で計算')
    notify('成績レポート', '\n'.join(lines))


if __name__ == '__main__':
    main()
