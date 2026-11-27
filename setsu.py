"""今節（同じ会場で続けて走る4〜7日間）の材料

節の間は選手とモーターの組み合わせが変わらないので、前日までの今節の結果から
「その節のモーターの出来・選手の調子・会場の今の傾向」が分かる。
どの特徴量も「その日より前の今節の結果」だけを使う（当日の結果は使わない）。

ss_n       : 今節すでに走った回数（初日は0）
ss_rank    : 今節の平均着順
ss_top3    : 今節の3着以内率
ss_st      : 今節の平均スタートタイミング
ss_tenji_d : 今日の展示タイム − 今節の展示タイムの平均（マイナスなら仕上がりが良くなった）
vs_cw      : この会場の今節、このコースの1着率 − いつもの1着率（ならしてある）
ss_bad     : 今節の着順なしの回数（落水・転覆・失格・F・欠場など）
"""
import os

import numpy as np
import pandas as pd

SETSU = ['ss_n', 'ss_rank', 'ss_top3', 'ss_st', 'ss_tenji_d', 'vs_cw', 'ss_bad']
GAP_RACER = 2   # 同じ会場で何日空いたら別の節とみなすか（選手）
GAP_VENUE = 1   # 同じ（会場）
SMOOTH = 10     # 会場の今節傾向のならし（レース数）
RACER_COLS = ['n', 'rank_sum', 'rank_n', 'top3', 'st_sum', 'st_n', 'tj_sum', 'tj_n']


def _ord(d):
    return pd.to_datetime(d.astype(int).astype(str), format='%Y%m%d').map(
        pd.Timestamp.toordinal)


def _racer_daily(df):
    x = df.assign(r=df['rank'], st2=df['st_ok'])
    g = x.groupby(['racer', 'venue', 'date']).agg(
        n=('boat', 'size'), rank_sum=('r', 'sum'), rank_n=('r', 'count'),
        top3=('top3', 'sum'), st_sum=('st2', 'sum'), st_n=('st2', 'count'),
        tj_sum=('tenji', 'sum'), tj_n=('tenji', 'count')).reset_index()
    g = g.sort_values(['racer', 'venue', 'date'])
    o = _ord(g['date'])
    prev = o.groupby([g['racer'], g['venue']]).shift()
    g['meet'] = ((o - prev).isna() | (o - prev > GAP_RACER)).cumsum()
    return g


def _venue_daily(df):
    g = df.groupby(['venue', 'date', 'course']).agg(
        w=('win', 'sum'), cn=('win', 'size')).reset_index()
    days = g[['venue', 'date']].drop_duplicates().sort_values(['venue', 'date'])
    o = _ord(days['date'])
    prev = o.groupby(days['venue']).shift()
    days['vmeet'] = ((o - prev).isna() | (o - prev > GAP_VENUE)).cumsum()
    return g.merge(days, on=['venue', 'date'])


def add_hist(df):
    """学習・比較用：各行に今節の特徴量を付ける（その日より前の今節の結果だけ）。
    df には rank, top3, win, st_ok, tenji, course, vc_win が必要"""
    g = _racer_daily(df)
    cum = g.groupby('meet')[RACER_COLS].cumsum() - g[RACER_COLS]
    f = g[['racer', 'venue', 'date']].copy()
    f['ss_n'] = cum['n']
    f['ss_rank'] = cum['rank_sum'] / cum['rank_n'].where(cum['rank_n'] > 0)
    f['ss_top3'] = cum['top3'] / cum['n'].where(cum['n'] > 0)
    f['ss_st'] = cum['st_sum'] / cum['st_n'].where(cum['st_n'] > 0)
    f['_tj'] = cum['tj_sum'] / cum['tj_n'].where(cum['tj_n'] > 0)
    f['ss_bad'] = cum['n'] - cum['rank_n']
    df = df.merge(f, on=['racer', 'venue', 'date'], how='left')
    df['ss_tenji_d'] = df['tenji'] - df['_tj']
    df = df.drop(columns=['_tj'])

    v = _venue_daily(df)
    v = v.sort_values(['vmeet', 'course', 'date'])
    grp = v.groupby(['vmeet', 'course'])
    v['cw'] = grp['w'].cumsum() - v['w']
    v['cn2'] = grp['cn'].cumsum() - v['cn']
    df = df.merge(v[['venue', 'date', 'course', 'cw', 'cn2']],
                  on=['venue', 'date', 'course'], how='left')
    df['vs_cw'] = _trend(df['cw'], df['cn2'], df['vc_win'])
    return df.drop(columns=['cw', 'cn2'])


def _trend(w, n, base):
    w = w.fillna(0)
    n = n.fillna(0)
    b = base.fillna(0)
    return ((w + SMOOTH * b) / (n + SMOOTH) - b).where(base.notna(), 0.0)


def write_tables(df, model_dir):
    """予想用：各選手・各会場の「最新の節」の合計を保存する（前日までの結果）"""
    g = _racer_daily(df)
    last = g.groupby('meet')
    t = last[RACER_COLS].sum()
    t[['racer', 'venue']] = last[['racer', 'venue']].first()
    t['last'] = last['date'].max()
    # 選手・会場ごとに一番新しい節だけ
    t = t.sort_values('last').drop_duplicates(['racer', 'venue'], keep='last')
    t[['racer', 'venue', 'last'] + RACER_COLS].to_csv(
        os.path.join(model_dir, 'cur_setsu.csv'), index=False)

    v = _venue_daily(df)
    lastm = v.groupby('venue')['vmeet'].transform('max')
    v = v[v['vmeet'] == lastm]
    vt = v.groupby(['venue', 'course']).agg(w=('w', 'sum'), cn=('cn', 'sum'),
                                            last=('date', 'max')).reset_index()
    vt.to_csv(os.path.join(model_dir, 'cur_setsu_venue.csv'), index=False)
    return t, vt


def load_tables(model_dir):
    """予想用の表を読む。無ければ空（今節の材料は「初日」扱いになる）"""
    out = {}, {}
    p1 = os.path.join(model_dir, 'cur_setsu.csv')
    p2 = os.path.join(model_dir, 'cur_setsu_venue.csv')
    if os.path.exists(p1):
        t = pd.read_csv(p1)
        out[0].update({(int(r.racer), int(r.venue)): r for r in t.itertuples()})
    if os.path.exists(p2):
        t = pd.read_csv(p2)
        out[1].update({(int(r.venue), int(r.course)): r for r in t.itertuples()})
    return out


def lookup(tables, racer, venue, course, tenji, vc_win, hd):
    """予想用：1艇分の今節の特徴量"""
    rt, vt = tables
    today = pd.Timestamp(str(hd)).toordinal()
    f = dict(ss_n=0.0, ss_rank=np.nan, ss_top3=np.nan, ss_st=np.nan,
             ss_tenji_d=np.nan, vs_cw=0.0, ss_bad=0.0)
    r = rt.get((int(racer), int(venue)))
    if r is not None and 0 < today - pd.Timestamp(str(int(r.last))).toordinal() <= GAP_RACER:
        f['ss_n'] = float(r.n)
        f['ss_bad'] = float(r.n - r.rank_n)
        f['ss_rank'] = r.rank_sum / r.rank_n if r.rank_n else np.nan
        f['ss_top3'] = r.top3 / r.n if r.n else np.nan
        f['ss_st'] = r.st_sum / r.st_n if r.st_n else np.nan
        tj = r.tj_sum / r.tj_n if r.tj_n else np.nan
        if tenji is not None and tenji == tenji:
            f['ss_tenji_d'] = tenji - tj
    v = vt.get((int(venue), int(course)))
    if v is not None and vc_win is not None and vc_win == vc_win and \
            0 < today - pd.Timestamp(str(int(v.last))).toordinal() <= GAP_VENUE:
        f['vs_cw'] = (v.w + SMOOTH * vc_win) / (v.cn + SMOOTH) - vc_win
    return f


def hot_list(tables, hd, top=5):
    """通知用：今節好調な選手（3走以上で平均着順が良い順）"""
    rt, _ = tables
    today = pd.Timestamp(str(hd)).toordinal()
    rows = []
    for (racer, venue), r in rt.items():
        if r.n >= 3 and r.rank_n and \
                0 < today - pd.Timestamp(str(int(r.last))).toordinal() <= GAP_RACER:
            rows.append((r.rank_sum / r.rank_n, r.top3 / r.n, racer, venue, int(r.n)))
    rows.sort()
    return rows[:top]
