"""追加の材料（フライング歴・当日の前走結果）

f_180  : その日より前の180日間のフライング（F）の回数。Fを持つ選手はスタートを控えやすい
td_n   : 今日、このレースより前に走った回数（同じ会場）
td_win : そのうち1着の回数
td_top3: そのうち3着以内の回数
どれも「このレースより前」の結果だけを使う。
学習用は成績ファイルから、予想用は当日の結果の記録（results_日付.csv）と
出走の記録（entries_日付.csv）から同じ意味で作る。
"""
import os

import numpy as np
import pandas as pd

EXTRA = ['f_180', 'td_n', 'td_win', 'td_top3']
F_DAYS = 180


def _ord(d):
    return pd.to_datetime(d.astype(int).astype(str), format='%Y%m%d').map(
        pd.Timestamp.toordinal).values


def add_hist(df):
    """学習・比較用。df には date, venue, race, racer, rank, rank_raw が必要"""
    df = df.copy()
    # フライング：その日より前の180日間の回数
    f = df[df['rank_raw'].astype(str).str.upper().str.startswith('F')]
    fd = f.groupby('racer')['date'].apply(lambda s: np.sort(_ord(s))).to_dict()
    o = _ord(df['date'])
    cnt = np.zeros(len(df))
    for i, (r, d) in enumerate(zip(df['racer'].values, o)):
        a = fd.get(r)
        if a is not None:
            cnt[i] = np.searchsorted(a, d, 'left') - np.searchsorted(a, d - F_DAYS, 'left')
    df['f_180'] = cnt
    # 当日の前走（同じ日・同じ会場・より前のレース番号）
    s = df.sort_values(['racer', 'date', 'venue', 'race'])
    g = s.groupby(['racer', 'date', 'venue'])
    win = (s['rank'] == 1).astype(int)
    top3 = (s['rank'] <= 3).astype(int)
    s = s.assign(td_n=g.cumcount(),
                 td_win=win.groupby([s['racer'], s['date'], s['venue']]).cumsum() - win,
                 td_top3=top3.groupby([s['racer'], s['date'], s['venue']]).cumsum() - top3)
    df[['td_n', 'td_win', 'td_top3']] = s[['td_n', 'td_win', 'td_top3']].astype(float)
    return df


def write_tables(df, model_dir, today):
    """予想用：選手ごとの、今日から見て180日以内のFの日付（前日まで）"""
    f = df[df['rank_raw'].astype(str).str.upper().str.startswith('F')]
    lo = pd.Timestamp(str(today)).toordinal() - F_DAYS
    o = _ord(f['date']) if len(f) else np.array([])
    f = f[o >= lo][['racer', 'date']]
    f.to_csv(os.path.join(model_dir, 'cur_f.csv'), index=False)


def load_f(model_dir):
    p = os.path.join(model_dir, 'cur_f.csv')
    if not os.path.exists(p):
        return {}
    t = pd.read_csv(p)
    out = {}
    for r, d in zip(t['racer'], t['date']):
        out.setdefault(int(r), []).append(int(d))
    return out


def today_results(logs, hd):
    """予想用：今日すでに結果が出たレースの、選手ごとの着（1〜3着のみ分かる）
    戻り値 {(会場, R): {選手: 着(1〜3) または 0(4着以下)}}"""
    rp = os.path.join(logs, f'results_{hd}.csv')
    ep = os.path.join(logs, f'entries_{hd}.csv')
    if not (os.path.exists(rp) and os.path.exists(ep)):
        return {}
    r = pd.read_csv(rp, dtype=str)
    r = r[(r['hd'] != 'hd') & r['tri'].str.match(r'^\d-\d-\d$', na=False)]
    e = pd.read_csv(ep, dtype=str)
    e = e[e['hd'] != 'hd'].drop_duplicates(['jcd', 'rno', 'boat'], keep='last')
    tri = {(int(j), int(n)): t for j, n, t in zip(r['jcd'], r['rno'], r['tri'])}
    out = {}
    for j, n, b, rid in zip(e['jcd'], e['rno'], e['boat'], e['racer']):
        k = (int(j), int(n))
        if k not in tri:
            continue
        order = [int(x) for x in tri[k].split('-')]
        b = int(b)
        out.setdefault(k, {})[int(rid)] = order.index(b) + 1 if b in order else 0
    return out


def lookup(fdates, done, racer, venue, rno, hd):
    """予想用：1艇分の追加の材料"""
    today = pd.Timestamp(str(hd)).toordinal()
    ds = fdates.get(int(racer), [])
    f180 = sum(1 for d in ds if 0 < today - pd.Timestamp(str(d)).toordinal() <= F_DAYS)
    n = w = t3 = 0
    for (j, r), res in done.items():
        if j == int(venue) and r < int(rno) and int(racer) in res:
            n += 1
            w += res[int(racer)] == 1
            t3 += 1 <= res[int(racer)] <= 3
    return dict(f_180=float(f180), td_n=float(n), td_win=float(w), td_top3=float(t3))
