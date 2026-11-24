"""通知した買い目の成績を集計し、表（CSV）に保存して通知する。

logs/all_bets.csv : 全買い目と結果の一覧
logs/daily.csv    : 日ごとの成績表
"""
import os
import glob
import datetime

import pandas as pd

from common import LOGS, MODEL, now_jst, download_k, read_k, notify


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
    log = pd.read_csv(os.path.join(LOGS, f'{hd}.csv'), dtype=str)
    # マージで見出し行が重複した場合は取り除く
    log = log[log['hd'] != 'hd'].copy()
    log['jcd'] = log['jcd'].astype(int)
    log['rno'] = log['rno'].astype(int)
    # 同じレース・同じ買い目が重複して記録されていたら1つにまとめる
    n0 = len(log)
    log = log.drop_duplicates(subset=['hd', 'jcd', 'rno', 'pick'],
                              keep='first')
    if len(log) < n0:
        print(hd, '重複を取り除いた行数', n0 - len(log))
    rc = results_for(hd)
    if rc is None:
        return None, 0
    races = log[['jcd', 'rno']].drop_duplicates().shape[0]
    bets = log[log['pick'].fillna('') != ''].copy()
    bets = bets.merge(rc[['jcd', 'rno', 'tri', 'tri_pay']],
                      on=['jcd', 'rno'], how='left')
    bets = bets[bets['tri'].notna()]  # 中止・返還は除く
    bets['prob'] = pd.to_numeric(bets['prob'], errors='coerce')
    bets = bets.sort_values(['jcd', 'rno', 'prob'],
                            ascending=[True, True, False])
    bets['何点目'] = bets.groupby(['jcd', 'rno']).cumcount() + 1
    hist = model_history()
    bets['モデル'] = [version_of(r, hist) for _, r in bets.iterrows()]
    bets['hit'] = (bets['pick'] == bets['tri']).astype(int)
    bets['ret'] = bets['tri_pay'].where(bets['hit'] == 1, 0)
    return bets, races


# モデルの切り替え履歴（この日時以降の予想は、このモデル）
BASE_HISTORY = [
    (0, 'v1 1着モデル'),
    (202611232125, 'v2 3モデル(11/23 Colab)'),
]
BANDS = [0, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 1.01]


def model_history():
    h = list(BASE_HISTORY)
    path = os.path.join(MODEL, 'history.csv')
    if os.path.exists(path):
        t = pd.read_csv(path, dtype=str)
        h += [(int(r['from']), r['version']) for _, r in t.iterrows()]
    return sorted(h)


def version_of(row, hist):
    key = int(str(row['hd']) + str(row['run_at']).replace(':', '')[:4])
    v = hist[0][1]
    for start, name in hist:
        if key >= start:
            v = name
    return v


def calibration(ab):
    """予測確率の帯ごとに、実際の的中率と回収率を集計する"""
    t = ab.copy()
    t['帯'] = pd.cut(t['prob'].astype(float), BANDS, right=False,
                    labels=[f'{a*100:.0f}-{b*100:.0f}%' if b < 1 else
                            f'{a*100:.0f}%以上'
                            for a, b in zip(BANDS[:-1], BANDS[1:])])
    g = t.groupby(['モデル', '帯'], observed=True)
    c = pd.DataFrame({
        '点数': g.size(),
        '予測の平均%': (g['prob'].mean() * 100).round(1),
        '実際の的中率%': (g['hit'].mean() * 100).round(1),
        '回収率%': (g['ret'].sum() / (g.size() * 100) * 100).round(1),
    }).reset_index()
    r = t.groupby(['モデル', '何点目'])
    k = pd.DataFrame({
        '点数': r.size(),
        '的中率%': (r['hit'].mean() * 100).round(1),
        '回収率%': (r['ret'].sum() / (r.size() * 100) * 100).round(1),
    }).reset_index()
    return c, k


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
    cols = ['日付', '会場', 'R', '締切', '買い目', '何点目', '確率',
            'オッズ', '期待値', '結果', '的中', '払戻', 'モデル']
    ab[cols].to_csv(os.path.join(LOGS, 'all_bets.csv'), index=False)

    # 確率の答え合わせ（校正）と、1点目・2点目の比較
    raw = pd.concat(all_bets, ignore_index=True)
    cal, by_rank = calibration(raw)
    cal.to_csv(os.path.join(LOGS, 'calibration.csv'), index=False)
    by_rank.to_csv(os.path.join(LOGS, 'by_rank.csv'), index=False)

    lines = []
    if day is not None:
        lines.append(f'{hd[4:6]}/{hd[6:]}: ' + fmt(races_day, day))
    lines.append('累計: ' + fmt(races_total, ab.rename(
        columns={'的中': 'hit', '払戻': 'ret'})))
    lines.append(f'累計収支: {int(dd["累計収支"].iloc[-1]):+,}円（1点100円）')
    cur = raw['モデル'].iloc[-1] if len(raw) else ''
    c = cal[(cal['モデル'] == cur) & (cal['点数'] > 0)]
    if len(c):
        lines.append(f'確率の答え合わせ（{cur}）:')
        for _, r in c.iterrows():
            lines.append(f' {r["帯"]}: 予測{r["予測の平均%"]}% → '
                         f'実際{r["実際の的中率%"]}%（{int(r["点数"])}点）')
    k = by_rank[by_rank['モデル'] == cur]
    for _, r in k.iterrows():
        lines.append(f' {int(r["何点目"])}点目: 的中{r["的中率%"]}% '
                     f'回収{r["回収率%"]}%（{int(r["点数"])}点）')
    notify('成績レポート', '\n'.join(lines))


if __name__ == '__main__':
    main()
