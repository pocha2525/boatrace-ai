"""番組表（出走表）の材料：級別・全国/当地の勝率と2連率・モーター/ボートの2連率・年齢・体重、
それと風向。

学習用は公式の番組表ファイル（Bファイル、過去分あり）から、
予想用は公式サイトの出走表ページから、同じ意味の数字を作る。
"""
import os
import re
import math
import tempfile
import unicodedata

import numpy as np
import pandas as pd

from common import DATA, MB, get

BCOLS = ['b_cls', 'b_nat_win', 'b_nat_2r', 'b_loc_win', 'b_loc_2r',
         'b_mot_2r', 'b_boat_2r', 'b_age', 'b_wt']
BREL = ['b_cls_d', 'b_nat_win_d', 'b_nat_rank', 'b_loc_d', 'b_mot_rank',
        'b_mot_d', 'b_boat_d']
WIND = ['wd_x', 'wd_y']
BFEATS = BCOLS + BREL + WIND
CLS = {'A1': 4, 'A2': 3, 'B1': 2, 'B2': 1}

# 16方位（北から時計回り）
DIRS = ['北', '北北東', '北東', '東北東', '東', '東南東', '南東', '南南東',
        '南', '南南西', '南西', '西南西', '西', '西北西', '北西', '北北西']


def b_path(d):
    return os.path.join(DATA, 'B', f'B{d:%y%m%d}.TXT')


def download_b(d):
    path = b_path(d)
    if os.path.exists(path):
        return path
    r = get(f'{MB}B/{d:%Y%m}/b{d:%y%m%d}.lzh')
    if r is None or not r.content:
        return None
    import lhafile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.lzh', delete=False) as tmp:
        tmp.write(r.content)
        name = tmp.name
    try:
        f = lhafile.Lhafile(name)
        if not f.namelist():
            return None
        with open(path, 'wb') as out:
            out.write(f.read(f.namelist()[0]))
    finally:
        os.remove(name)
    return path


ROW = re.compile(
    r'^([1-6]) (\d{4})(.{4})(\d{2})(.{2})(\d{2})([AB][12])\s*([\d.]+)\s+([\d.]+)'
    r'\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)')


def parse_b_text(text, date):
    venue = race = None
    rows = []
    for raw in text.splitlines():
        m = re.match(r'^(\d{2})BBGN', raw.strip())
        if m:
            venue, race = int(m.group(1)), None
            continue
        line = unicodedata.normalize('NFKC', raw)
        m = re.match(r'^\s*(\d{1,2})R\s', line)
        if m and '締切' in line:
            race = int(m.group(1))
            continue
        if race is None:
            continue
        m = ROW.match(raw)
        if m:
            g = m.groups()
            rows.append({'date': date, 'venue': venue, 'race': race,
                         'boat': int(g[0]), 'racer': int(g[1]),
                         'b_age': float(g[3]), 'b_wt': float(g[5]),
                         'b_cls': CLS[g[6]],
                         'b_nat_win': float(g[7]), 'b_nat_2r': float(g[8]),
                         'b_loc_win': float(g[9]), 'b_loc_2r': float(g[10]),
                         'b_mot_2r': float(g[12]), 'b_boat_2r': float(g[14])})
    return rows


def read_b(path):
    date = int('20' + os.path.basename(path)[1:7])
    with open(path, encoding='cp932', errors='ignore') as f:
        return parse_b_text(f.read(), date)


def load_all(days):
    rows = []
    for d in days:
        p = b_path(d)
        if os.path.exists(p):
            rows += read_b(p)
    return pd.DataFrame(rows)


def add_brel(d, keys=None):
    """レース内での比較（学習と予想で共通）"""
    g = d.groupby(keys) if keys else None

    def mean(c):
        return g[c].transform('mean') if g is not None else d[c].mean()

    def rank(c):
        return g[c].rank(ascending=False) if g is not None else d[c].rank(ascending=False)
    d['b_cls_d'] = d['b_cls'] - mean('b_cls')
    d['b_nat_win_d'] = d['b_nat_win'] - mean('b_nat_win')
    d['b_nat_rank'] = rank('b_nat_win')
    d['b_loc_d'] = d['b_loc_win'] - d['b_nat_win']
    d['b_mot_rank'] = rank('b_mot_2r')
    d['b_mot_d'] = d['b_mot_2r'] - mean('b_mot_2r')
    d['b_boat_d'] = d['b_boat_2r'] - mean('b_boat_2r')
    return d


def wind_xy(dir_name, wind):
    """風向（16方位の名前）と風速から、東西・南北の成分を作る。無風・不明は0"""
    try:
        i = DIRS.index(str(dir_name).strip())
    except ValueError:
        return 0.0, 0.0
    a = i * math.pi / 8
    w = wind if wind == wind and wind is not None else 0.0
    return round(w * math.sin(a), 3), round(w * math.cos(a), 3)


def add_hist(df, rc, days):
    """学習用：成績の行に番組表の数字と風向を付ける"""
    b = load_all(days)
    keys = ['date', 'venue', 'race', 'boat']
    if len(b):
        b = b.drop_duplicates(keys)
        # 選手が一致する行だけ使う（欠場・変更の食い違いを防ぐ）
        df = df.merge(b, on=keys, how='left', suffixes=('', '_b'))
        bad = df['racer_b'].notna() & (df['racer_b'] != df['racer'])
        df.loc[bad, BCOLS] = np.nan
        df = df.drop(columns=['racer_b'])
    else:
        for c in BCOLS:
            df[c] = np.nan
    df = add_brel(df, ['date', 'venue', 'race'])
    k = ['date', 'venue', 'race']
    w = rc[k + ['wind_dir', 'wind']].drop_duplicates(k).copy()
    xy = [wind_xy(a, b_) for a, b_ in zip(w['wind_dir'], w['wind'])]
    w['wd_x'] = [p[0] for p in xy]
    w['wd_y'] = [p[1] for p in xy]
    df = df.drop(columns=[c for c in WIND if c in df.columns])
    return df.merge(w[k + WIND], on=k, how='left')


# ---------- 予想用（公式サイトの出走表ページ） ----------

RL = re.compile(
    r'(\d{4}) / \|(A1|A2|B1|B2)\|[^|]*\|[^|]*\|(\d+)歳/([\d.]+)kg \|F\d+ \|L\d+ \|'
    r'[^|]*\|([\d.-]+) \|([\d.-]+) \|[^|]*\|([\d.-]+) \|([\d.-]+) \|[^|]*\|'
    r'(\d+) \|([\d.-]+) \|[^|]*\|(\d+) \|([\d.-]+) \|')


def _f(s):
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_racelist(html):
    """出走表ページから {登録番号: 数字} を作る"""
    body = re.sub(r'<script.*?</script>', '', html, flags=re.S)
    txt = re.sub(r'<[^>]+>', '|', body)
    txt = re.sub(r'\s+', ' ', txt)
    txt = re.sub(r'(\| ?)+', '|', txt)
    out = {}
    for m in RL.finditer(txt):
        g = m.groups()
        out[int(g[0])] = {
            'b_cls': CLS[g[1]], 'b_age': _f(g[2]), 'b_wt': float(math.floor(_f(g[3]))),
            'b_nat_win': _f(g[4]), 'b_nat_2r': _f(g[5]),
            'b_loc_win': _f(g[6]), 'b_loc_2r': _f(g[7]),
            'b_mot_2r': _f(g[9]), 'b_boat_2r': _f(g[11])}
    return out


# 公式サイトの風向の画像番号（is-windN）→ 16方位の名前。
# 成績ファイルの風向と突き合わせて確かめた対応（model/wind_img_map.csv）を使う。
_IMG = None


def img_dir(n):
    """画像番号から風向の名前。対応が確かめられていなければ None"""
    global _IMG
    if _IMG is None:
        _IMG = {}
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model',
                         'wind_img_map.csv')
        if os.path.exists(p):
            t = pd.read_csv(p)
            _IMG = dict(zip(t['img'].astype(int), t['dir']))
    try:
        return _IMG.get(int(n))
    except (TypeError, ValueError):
        return None
