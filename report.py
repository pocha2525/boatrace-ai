"""通知した買い目の成績を集計し、表（CSV）に保存して通知する。

logs/all_bets.csv : 全買い目と結果の一覧
logs/daily.csv    : 日ごとの成績表
"""
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


def day_bets(hd):
    """その日の記録に結果を付けて返す（結果がまだ無ければ None）。"""
    log = pd.read_csv(os.path.join(LOGS, f'{hd}.csv'), dtype={'pick': str})
    rc = results_for(hd)
    if rc is None:
        return None, 0
    races = log[['jcd', 'rno']].drop_duplicates().shape[0]
    bets = log[log['pick'].fillna('') != ''].copy()
    bets = bets.merge(rc[['jcd', 'rno', 'tri', 'tri_pay']],
                      on=['jcd', 'rno'], how='left')
    bets = bets[bets['tri'].notna()]  # 中止・返還は除く
    bets['hit'] = (bets['pick'] == bets['tri']).astype(int)
    bets['ret'] = bets['tri_pay'].where(bets['hit'] == 1, 0)
    return bets, races


def fmt(n_races, b):
    n = len(b)
    hits = int(b['hit'].sum()) if n else 0
    ret = float(b['ret'].sum()) if n else 0.0
    hr = hits / n * 100 if n else 0
    roi = ret / (n * 100) * 100 if n else 0
    return (f'予想{n_races}R / {n}点 / 的中{hits}（{hr:.1f}%）'
            f' / 回収率{roi:.1f}%')


def main():
    hd = os.environ.get('HD') or \
        f'{now_jst().date() - datetime.timedelta(days=1):%Y%m%d}'
    all_bets = []
    daily = []
    races_total = 0
    races_day = 0
    day = None
    for path in sorted(glob.glob(os.path.join(LOGS, '2*.csv'))):
        h = os.path.basename(path)[:8]
        if h > hd:
            continue
        b, n_races = day_bets(h)
        if b is None:
            continue
        races_total += n_races
        if h == hd:
            day, races_day = b, n_races
        all_bets.append(b)
        n = len(b)
        daily.append({
            '日付': f'{h[:4]}/{h[4:6]}/{h[6:]}',
            '予想レース数': n_races,
            '点数': n,
            '的中数': int(b['hit'].sum()),
            '投資': n * 100,
            '払戻': int(b['ret'].sum()),
        })
    if not daily:
        print('集計できる記録がまだありません')
        return

    # 日ごとの成績表
    dd = pd.DataFrame(daily)
    dd['的中率%'] = (dd['的中数'] / dd['点数'].where(dd['点数'] > 0)
                   * 100).round(1)
    dd['回収率%'] = (dd['払戻'] / dd['投資'].where(dd['投資'] > 0)
                   * 100).round(1)
    dd['累計投資'] = dd['投資'].cumsum()
    dd['累計払戻'] = dd['払戻'].cumsum()
    dd['累計回収率%'] = (dd['累計払戻'] / dd['累計投資'].where(
        dd['累計投資'] > 0) * 100).round(1)
    dd['収支'] = dd['払戻'] - dd['投資']
    dd['累計収支'] = dd['収支'].cumsum()
    dd.to_csv(os.path.join(LOGS, 'daily.csv'), index=False)

    # 全買い目の一覧
    ab = pd.concat(all_bets, ignore_index=True)
    ab = ab.rename(columns={
        'hd': '日付', 'venue': '会場', 'rno': 'R', 'deadline': '締切',
        'pick': '買い目', 'prob': '確率', 'odds': 'オッズ', 'ev': '期待値',
        'tri': '結果', 'hit': '的中', 'ret': '払戻'})
    cols = ['日付', '会場', 'R', '締切', '買い目', '確率', 'オッズ',
            '期待値', '結果', '的中', '払戻']
    ab[cols].to_csv(os.path.join(LOGS, 'all_bets.csv'), index=False)

    lines = []
    if day is not None:
        lines.append(f'{hd[4:6]}/{hd[6:]}: ' + fmt(races_day, day))
    lines.append('累計: ' + fmt(races_total, ab.rename(
        columns={'的中': 'hit', '払戻': 'ret'})))
    lines.append(f'累計収支: {int(dd["累計収支"].iloc[-1]):+,}円（1点100円）')
    notify('成績レポート', '\n'.join(lines))


if __name__ == '__main__':
    main()
