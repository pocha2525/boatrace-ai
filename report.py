"""通知した買い目の成績を集計し、表（CSV）に保存して通知する。

logs/all_bets.csv : 全買い目と結果の一覧
logs/daily.csv    : 日ごとの成績表
logs/judged_all.csv : 判定した全レース（買い・見送り）と実際の結果
"""
import os
import glob
import datetime

import pandas as pd

from common import LOGS, MODEL, now_jst, download_k, read_k, notify


_RESULTS = {}


def results_for(hd):
    """その日の全レースの結果（同じ日は1回だけ読む）"""
    if hd not in _RESULTS:
        _RESULTS[hd] = _results_for(hd)
    return _RESULTS[hd]


def _results_for(hd):
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
    from settlement import settle
    columns = ['jcd', 'rno', 'tri', 'tri_pay'] + [c for c in
               ['tri_payouts', 'refund_boats', 'race_status'] if c in rc]
    bets = bets.merge(rc[columns], on=['jcd', 'rno'], how='left')
    outcomes = [settle(row['pick'], row) for _, row in bets.iterrows()]
    bets['settlement'] = [x[0] for x in outcomes]
    bets['hit'] = [x[1] for x in outcomes]
    bets['ret'] = [x[2] for x in outcomes]
    bets['tri'] = [x[3] for x in outcomes]
    pending = int((bets['settlement'] == 'pending').sum())
    bets = bets[bets['settlement'] != 'pending'].copy()
    bets['prob'] = pd.to_numeric(bets['prob'], errors='coerce')
    bets = bets.sort_values(['jcd', 'rno', 'prob'],
                            ascending=[True, True, False])
    bets['何点目'] = bets.groupby(['jcd', 'rno']).cumcount() + 1
    hist = model_history()
    bets['モデル'] = [version_of(r, hist) for _, r in bets.iterrows()]
    bets[['hit', 'ret']] = bets[['hit', 'ret']].astype(int)
    bets.attrs['pending'] = pending
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


JUDGED = os.path.join(LOGS, 'judged_all.csv')
NO_TRI = '3連単未確認'   # 結果行はあるが、返還/中止は組番の欠損だけで断定しない
MISSING = '結果未取得'    # 成績ファイルにまだ無い（あとでもう一度照合する）
JUDGED_COLS = ['日付', '締切', '会場', 'R', '判定', '朝の候補',
               '本命', '本命オッズ', '本命期待値', '2番手', '2番手オッズ', '2番手期待値',
               '結果', '払戻', '本命当たり', '当たり', '見送り理由']


def judged_day(h):
    """その日に判定した全レースに、実際の結果と買った点数を付ける（結果がまだ無ければ None）"""
    t = pd.read_csv(os.path.join(LOGS, f'notify_{h}.csv'), dtype=str)
    t = t[t['notify_id'] != 'notify_id']
    t = t.drop_duplicates('notify_id', keep='last').copy()
    if len(t) == 0:
        return None
    rc = results_for(h)
    if rc is None:
        return None
    t['jcd'] = t['jcd'].astype(int)
    t['rno'] = t['rno'].astype(int)
    t = t.merge(rc[['jcd', 'rno', 'tri', 'tri_pay']], on=['jcd', 'rno'],
                how='left', indicator=True)
    # 結果の状態：成績ファイルにレースがあり3連単が無い → 不成立・返還など
    #             成績ファイルにレース自体が無い → 結果未取得（後でもう一度照合する）
    t['tri'] = t['tri'].where(t['tri'].notna(),
                              t['_merge'].map({'both': NO_TRI, 'left_only': MISSING}))
    t = t.drop(columns=['_merge'])
    # 実際に買った買い目（成績記録 logs/日付.csv）
    bought = {}
    bp = os.path.join(LOGS, f'{h}.csv')
    if os.path.exists(bp):
        b = pd.read_csv(bp, dtype=str)
        b = b[(b['hd'] != 'hd') & b['pick'].notna() & (b['pick'] != '')]
        for j, r, p in zip(b['jcd'], b['rno'], b['pick']):
            bought.setdefault((int(j), int(r)), set()).add(p)

    def part(s, i):
        a = str(s).split(' ') if isinstance(s, str) and s else []
        return a[i] if len(a) > i else ''
    out = pd.DataFrame({
        '日付': f'{h[:4]}/{h[4:6]}/{h[6:]}',
        '締切': t['deadline'],
        '会場': t['venue'],
        'R': t['rno'],
        '朝の候補': t['morning'].fillna(''),
        '本命': t['picks'].map(lambda s: part(s, 0)),
        '本命オッズ': t['odds'].map(lambda s: part(s, 0)),
        '本命期待値': t['evs'].map(lambda s: part(s, 0)),
        '2番手': t['picks'].map(lambda s: part(s, 1)),
        '2番手オッズ': t['odds'].map(lambda s: part(s, 1)),
        '2番手期待値': t['evs'].map(lambda s: part(s, 1)),
        '結果': t['tri'].fillna(MISSING),
        '払戻': t['tri_pay'].map(lambda x: str(int(x)) if pd.notna(x) else ''),
        '見送り理由': t['reason'].fillna('').where(t['status'] != '受付成功', ''),
    })
    if 'tri_payouts' in t:
        for index, payouts in t['tri_payouts'].items():
            if isinstance(payouts, dict) and len(payouts) > 1:
                out.loc[index, '結果'] = '/'.join(payouts)
                out.loc[index, '払戻'] = '/'.join(str(p) for p in payouts.values())
    keys = list(zip(t['jcd'], t['rno']))
    got = [bought.get(k, set()) for k in keys]
    st = t['status'].tolist()
    out['判定'] = [('買い%d点' % len(g) if g else '買い') if s == '受付成功'
                   else '見送り' if s == '条件未達' else s for s, g in zip(st, got)]
    out['本命当たり'] = ['◎' if pick in res.split('/') else '×'
                       for pick, res in zip(out['本命'], out['結果'])]
    mark = []
    for res, p1, p2, g in zip(out['結果'], out['本命'], out['2番手'], got):
        if res in (NO_TRI, MISSING):
            mark.append('-')
            continue
        winners = set(res.split('/'))
        m = '本命◎' if p1 in winners else '2番手◎' if p2 and p2 in winners else '×'
        mark.append(m + ('（的中）' if winners.intersection(g) else ''))
    out['当たり'] = mark
    out.loc[out['結果'].isin([NO_TRI, MISSING]), '本命当たり'] = '-'
    return out.sort_values('締切')[JUDGED_COLS]


def update_judged(hd):
    """まだ記録していない日の判定に結果を付けて、judged_all.csv に足す"""
    old = None
    done = set()
    keep = None
    if os.path.exists(JUDGED):
        old = pd.read_csv(JUDGED, dtype=str)
        old = old[old['日付'] != '日付']
        if set(JUDGED_COLS) - set(old.columns):
            old = None   # 列が増えたので全部作り直す
        else:
            # 古い表記「中止・返還」と「結果未取得」が残る日は、照合し直す
            redo = set(old.loc[old['結果'].isin(['中止・返還', '不成立・返還', '未確定', NO_TRI, MISSING]) | old['結果'].isna(), '日付'])
            keep = old[old['日付'].isin(redo)]   # 照合し直せなかったら元に戻す
            old = old[~old['日付'].isin(redo)]
            done = {d.replace('/', '') for d in old['日付']}
    new = []
    for path in sorted(glob.glob(os.path.join(LOGS, 'notify_*.csv'))):
        h = os.path.basename(path)[7:15]
        if h > hd or h in done:
            continue
        try:
            j = judged_day(h)
        except Exception as e:
            print('判定の記録を作れない', h, e)
            j = None
        if j is not None:
            new.append(j)
        elif keep is not None and (keep['日付'].str.replace('/', '') == h).any():
            new.append(keep[keep['日付'].str.replace('/', '') == h])
    if not new:
        return
    if old is not None:
        refreshed = {d for x in new for d in x['日付']}
        old = old[~old['日付'].isin(refreshed)]
    parts = ([old] if old is not None else []) + [x.astype(str) for x in new]
    pd.concat(parts, ignore_index=True)[JUDGED_COLS].to_csv(JUDGED, index=False)
    print('判定の記録に追加', sum(len(x) for x in new), '行')


def main():
    hd = os.environ.get('HD') or \
        f'{now_jst().date() - datetime.timedelta(days=1):%Y%m%d}'
    try:
        import policy_replay
        policy_replay.report(LOGS, results_for, hd)
    except Exception as e:
        print('締切前オッズの再検証を保留', type(e).__name__)
    all_bets = []
    daily = []
    races_total = 0
    races_day = 0
    day = None
    # 判定した全レース（見送りも）に実際の結果を付けて残す
    try:
        update_judged(hd)
    except Exception as e:
        print('判定の記録の更新に失敗', e)

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
            '未確定点数': b.attrs.get('pending', 0),
            '点数': n,
            '的中数': int(b['hit'].sum()),
            '投資': n * 100,
            '返還点数': int((b['settlement'] == 'refunded').sum()),
            '払戻': int(b['ret'].sum()),
        })
    if not daily:
        print('集計できる記録がまだありません')
        return

    import quality
    quality.report(LOGS, results_for, hd)

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
        'tri': '結果', 'hit': '的中', 'ret': '払戻', 'settlement': '精算'})
    cols = ['日付', '会場', 'R', '締切', '買い目', '何点目', '確率',
            'オッズ', '期待値', '結果', '的中', '払戻', 'モデル', '精算']
    ab[cols].to_csv(os.path.join(LOGS, 'all_bets.csv'), index=False)

    # 確率の答え合わせ（校正）と、1点目・2点目の比較
    raw = pd.concat(all_bets, ignore_index=True)
    cal, by_rank = calibration(raw)
    cal.to_csv(os.path.join(LOGS, 'calibration.csv'), index=False)
    by_rank.to_csv(os.path.join(LOGS, 'by_rank.csv'), index=False)

    lines = []
    pending_total = int(dd['未確定点数'].sum())
    if pending_total:
        lines.append(f'未確定{pending_total}点は集計保留（表示成績は確定分のみ）')
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
    notify('成績レポート', '\n'.join(lines), notification_id='report-' + hd)


if __name__ == '__main__':
    main()
