"""直前情報の材料：体重・チルト・プロペラ新品・部品交換・調整重量・スタート展示のST・安定板

公式サイトの直前情報ページから読む。過去分は少しずつ取りためる（backfill.py）。
学習に使えるだけ貯まったら、夜間の改善で挑戦者モデルに入れて比べる。
"""
import os
import re

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

BEFORE = ['bf_wt', 'bf_tilt', 'bf_prop', 'bf_parts', 'bf_adj',
          'ex_st', 'ex_f', 'ex_st_rank', 'ex_st_d', 'stab']
COLS = ['hd', 'jcd', 'rno', 'boat', 'racer', 'bf_wt', 'bf_tilt', 'bf_prop',
        'bf_parts', 'bf_adj', 'ex_course', 'ex_st', 'ex_f', 'wind_img',
        'wind', 'wave', 'stab', 'tenji']


def _f(s):
    try:
        return float(str(s).replace('kg', '').strip())
    except ValueError:
        return np.nan


PARTS = ['ピストン', 'リング', '電気', 'キャブ', 'シリンダ', 'シャフト', 'ギヤ', 'キャリボ']


def parts(cell):
    """部品交換の数（ピストンリングは1つと数える）"""
    if not cell:
        return 0
    n = cell.count('ピストンリング')
    rest = cell.replace('ピストンリング', '')
    return n + sum(rest.count(k) for k in PARTS)


def parse(html, hd, jcd, rno):
    """直前情報ページ → 艇ごとの行（6行）。読めなければ []"""
    if not html or 'is-w748' not in html:
        return []
    s = BeautifulSoup(html, 'html.parser')
    ids = list(dict.fromkeys(re.findall(r'toban=(\d{4})', html)))[:6]
    rows = []
    for i, tb in enumerate(s.select('table.is-w748 tbody')[:6]):
        t = [td.get_text(strip=True) for td in tb.select('td')]
        if len(t) < 13:
            return []
        rows.append({'hd': hd, 'jcd': jcd, 'rno': rno, 'boat': i + 1,
                     'racer': int(ids[i]) if i < len(ids) else None,
                     'bf_wt': _f(t[3]), 'tenji': _f(t[4]), 'bf_tilt': _f(t[5]),
                     'bf_prop': int('新' in t[6]),
                     'bf_parts': parts(t[7]),
                     'bf_adj': _f(t[12])})
    if len(rows) != 6:
        return []
    # スタート展示（コース順に、艇番とST。Fは「F.05」のように出る）
    ex = {}
    for c, tr in enumerate(s.select('table.is-w238 tbody tr')[:6]):
        num = tr.select_one('span.table1_boatImage1Number')
        txt = tr.get_text('|', strip=True).split('|')
        if not num or not num.get_text(strip=True).isdigit():
            continue
        st = txt[-1] if txt else ''
        f = st.upper().startswith('F')
        v = _f(st.lstrip('FfLl'))
        ex[int(num.get_text(strip=True))] = (c + 1, -v if f and v == v else v, int(f))
    w = re.search(r'is-wind(\d+)"', html)
    lab = re.findall(r'LabelData">\s*([\d.]+)\s*(m|cm)\s*<', html)
    wind = next((float(v) for v, u in lab if u == 'm'), np.nan)
    wave = next((float(v) for v, u in lab if u == 'cm'), np.nan)
    stab = int('安定板' in html)
    for r in rows:
        c, st, f = ex.get(r['boat'], (np.nan, np.nan, 0))
        r.update(ex_course=c, ex_st=st, ex_f=f,
                 wind_img=int(w.group(1)) if w else np.nan,
                 wind=wind, wave=wave, stab=stab)
    return rows


def add_rel(d, keys=None):
    if 'ex_st' not in d:
        d['ex_st'] = np.nan
    g = d.groupby(keys) if keys else None
    if g is not None:
        d['ex_st_rank'] = g['ex_st'].rank()
        d['ex_st_d'] = d['ex_st'] - g['ex_st'].transform('mean')
    else:
        d['ex_st_rank'] = d['ex_st'].rank()
        d['ex_st_d'] = d['ex_st'] - d['ex_st'].mean()
    return d


def path(root, hd):
    return os.path.join(root, f'before_{hd}.csv')


def append(root, rows):
    if not rows:
        return
    os.makedirs(root, exist_ok=True)
    p = path(root, rows[0]['hd'])
    new = not os.path.exists(p)
    if not new:
        with open(p, encoding='utf-8') as f:
            head = f.readline().strip().split(',')
        if head != COLS:   # 列が増えたときは、前の行に空の列を足して書き直す
            old = pd.read_csv(p)
            for c in COLS:
                if c not in old:
                    old[c] = np.nan
            old[COLS].to_csv(p, index=False)
    pd.DataFrame(rows)[COLS].to_csv(p, mode='a', header=new, index=False)


def load_hist(root):
    """取りためた直前情報（重複は最後の行を使う）"""
    import glob
    fs = sorted(glob.glob(os.path.join(root, 'before_*.csv')))
    if not fs:
        return pd.DataFrame(columns=COLS)
    d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    for c in COLS:
        if c not in d:
            d[c] = np.nan
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d.dropna(subset=['hd', 'jcd', 'rno', 'boat'])
    return d.drop_duplicates(['hd', 'jcd', 'rno', 'boat'], keep='last')
