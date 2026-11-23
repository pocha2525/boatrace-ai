"""締切が近いレースを予想し、期待値の高い買い目をスマホに通知する。"""
import os
import re
import csv
import datetime
from itertools import permutations

import pandas as pd
import lightgbm as lgb
from bs4 import BeautifulSoup

from common import (MODEL, LOGS, JST, VENUES, FEATS, X, BR, now_jst,
                    get, page, notify)

WINDOW_MIN = 13      # 締切まで何分以内のレースを予想するか
# 本命モード：本命がはっきりしたレースで、当たりやすい3連単を2点
EV_TH = 1.0          # 期待値がこれ以上の買い目だけ出す
MIN_PROB = 0.15      # 確率がこれ未満の買い目は出さない
MAX_BETS = 2         # 1レースあたりの最大点数
LOG_COLS = ['hd', 'jcd', 'venue', 'rno', 'deadline', 'pick',
            'prob', 'odds', 'ev', 'run_at']


# ---------- 読み込み ----------

def load_model():
    path = os.path.join(MODEL, 'model.txt')
    if not os.path.exists(path):
        return None, None
    bst = lgb.Booster(model_file=path)
    lk = {}
    for _, _, n in FEATS:
        t = pd.read_csv(os.path.join(MODEL, f'cur_{n}.csv'))
        ks = [c for c in t.columns if c != n]
        lk[n] = {tuple(r[:-1]): r[-1] for r in t[ks + [n]].values.tolist()}
    return bst, lk


# ---------- 公式サイトから取得 ----------

def today_venues(hd):
    r = get(f'{BR}index?hd={hd}')
    if r is None:
        return []
    r.encoding = 'utf-8'
    return sorted({int(x) for x in re.findall(r'jcd=(\d{2})', r.text)})


def deadlines(jcd, hd):
    """その会場の1R〜12Rの締切予定時刻を返す。"""
    html = page('racelist', jcd, 1, hd)
    s = BeautifulSoup(html, 'html.parser')
    times = []
    for tr in s.select('tr'):
        txt = tr.get_text(' ', strip=True)
        if '締切予定時刻' in txt:
            times = re.findall(r'\d{1,2}:\d{2}', txt)
            break
    if len(times) < 12:
        html = page('raceindex', jcd, 1, hd)
        times = re.findall(r'>\s*(\d{1,2}:\d{2})\s*<', html)
    out = {}
    base = datetime.datetime.strptime(hd, '%Y%m%d').replace(tzinfo=JST)
    for i, t in enumerate(times[:12]):
        h, m = map(int, t.split(':'))
        out[i + 1] = base.replace(hour=h, minute=m)
    return out


def race_info(jcd, rno, hd):
    b = page('beforeinfo', jcd, rno, hd)
    ids = list(dict.fromkeys(re.findall(r'toban=(\d{4})', b)))[:6]
    s = BeautifulSoup(b, 'html.parser')
    tj = []
    for tb in s.select('table.is-w748 tbody')[:6]:
        t = [td.get_text(strip=True) for td in tb.select('td')]
        tj.append(pd.to_numeric(t[4], errors='coerce') if len(t) > 4 else None)
    nums = [x.get_text(strip=True)
            for x in s.select('span.table1_boatImage1Number')]
    crs = {int(x): i + 1 for i, x in enumerate(nums) if x.isdigit()}
    w = re.findall(r'LabelData">\s*([\d.]+)\s*(m|cm)\s*<', b)
    wind = next((float(v) for v, u in w if u == 'm'), 0.0)
    wave = next((float(v) for v, u in w if u == 'cm'), 0.0)
    r = BeautifulSoup(page('racelist', jcd, rno, hd), 'html.parser')
    h2 = r.select('td.is-lineH2')
    n = len(h2) // 6
    mot = []
    for i in range(6):
        try:
            txt = h2[i * n + 3].get_text(' ', strip=True)
            mot.append(int(txt.split()[0]))
        except (IndexError, ValueError):
            mot.append(None)
    return ids, tj, crs, wind, wave, mot


def odds_map(jcd, rno, hd):
    o = BeautifulSoup(page('odds3t', jcd, rno, hd), 'html.parser')
    v = [td.get_text(strip=True) for td in o.select('td.oddsPoint')]
    m = {}
    if len(v) < 120:
        return m
    for i in range(1, 7):
        ot = [x for x in range(1, 7) if x != i]
        prs = [(s, t) for s in ot for t in ot if t != s]
        for r, (s, t) in enumerate(prs):
            m[f'{i}-{s}-{t}'] = pd.to_numeric(v[r * 6 + i - 1],
                                              errors='coerce')
    return m


# ---------- 予想 ----------

def predict(bst, lk, jcd, rno, hd):
    ids, tj, crs, wind, wave, mot = race_info(jcd, rno, hd)
    if len(ids) < 3:
        return None
    yr = int(hd[:4])

    def g(n, *k):
        return lk[n].get(tuple(k))

    rows = []
    for i, rid in enumerate(ids):
        rid = int(rid)
        b = i + 1
        c = crs.get(b, b)
        rows.append({
            'boat': b, 'course': c, 'venue': jcd,
            'tenji': tj[i] if i < len(tj) else None,
            'r_win': g('r_win', rid), 'r_top2': g('r_top2', rid),
            'r_st': g('r_st', rid),
            'rc_win': g('rc_win', rid, c), 'rc_top2': g('rc_top2', rid, c),
            'rv_win': g('rv_win', rid, jcd),
            'm_top2': g('m_top2', jcd, mot[i], yr),
            'vc_win': g('vc_win', jcd, c),
            'wind': wind, 'wave': wave,
        })
    d = pd.DataFrame(rows).astype(float)
    d['tenji_diff'] = d['tenji'] - d['tenji'].mean()
    d['tenji_rank'] = d['tenji'].rank()
    d['rwin_rank'] = d['r_win'].rank(ascending=False)
    d['rst_diff'] = d['r_st'] - d['r_st'].mean()
    d['p'] = bst.predict(d[X])
    d['p'] = d['p'] / d['p'].sum()
    return dict(zip(d['boat'].astype(int), d['p'].clip(0.001, 0.98)))


def combos(p, om):
    res = []
    for a, b, c in permutations(p, 3):
        q = p[a] * p[b] / (1 - p[a])
        q = q * p[c] / max(1 - p[a] - p[b], 0.01)
        k = f'{a}-{b}-{c}'
        o = om.get(k)
        if o == o and o and q >= 0.01:
            res.append((q * o, k, q, o))
    # 当たりやすい順（確率の高い順）に並べる
    return sorted(res, key=lambda x: -x[2])


# ---------- 記録 ----------

def log_path(hd):
    return os.path.join(LOGS, f'{hd}.csv')


def done_races(hd):
    path = log_path(hd)
    if not os.path.exists(path):
        return set()
    t = pd.read_csv(path)
    return set(zip(t['jcd'], t['rno']))


def write_log(hd, rows):
    os.makedirs(LOGS, exist_ok=True)
    path = log_path(hd)
    new = not os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


def run_race(bst, lk, jcd, rno, hd, dl, now):
    name = VENUES.get(jcd, str(jcd))
    p = predict(bst, lk, jcd, rno, hd)
    base = {'hd': hd, 'jcd': jcd, 'venue': name, 'rno': rno,
            'deadline': f'{dl:%H:%M}', 'run_at': f'{now:%H:%M}'}
    if p is None:
        print(name, rno, 'R 情報が取れませんでした')
        return
    res = combos(p, odds_map(jcd, rno, hd))  # 当たりやすい順
    # 一番当たりやすい組み合わせが条件を満たすレースだけ、上位2点を出す
    ok = res and res[0][2] >= MIN_PROB and res[0][0] >= EV_TH
    bets = res[:MAX_BETS] if ok else []
    rows = []
    for ev, k, q, o in bets:
        rows.append(dict(base, pick=k, prob=round(q, 4), odds=o,
                         ev=round(ev, 2)))
    if not rows:
        rows.append(dict(base, pick='', prob='', odds='', ev=''))
    write_log(hd, rows)

    top = sorted(p.items(), key=lambda x: -x[1])[:3]
    head = ' / '.join(f'{b}号艇{v * 100:.0f}%' for b, v in top)
    if bets:
        lines = [f'締切 {dl:%H:%M}', f'1着予想: {head}', '本命:']
        for ev, k, q, o in bets:
            lines.append(f'{k}  {o}倍  確率{q * 100:.1f}%  期待値{ev:.2f}')
        notify(f'{name} {rno}R', '\n'.join(lines))
    else:
        print(f'{name} {rno}R 見送り（{head}）')


def main():
    now = now_jst()
    hd = os.environ.get('HD') or f'{now:%Y%m%d}'
    manual = os.environ.get('MANUAL') == '1'
    bst, lk = load_model()
    if bst is None:
        msg = 'モデルがまだありません。先に train を実行してください。'
        if manual:
            notify('ボートレースAI', msg)
        print(msg)
        return

    venues = []
    done = done_races(hd)
    upcoming = []
    for jcd in today_venues(hd):
        dls = deadlines(jcd, hd)
        if not dls:
            continue  # 今日レースのない会場（前売りなど）は数えない
        venues.append(jcd)
        for rno, dl in dls.items():
            mins = (dl - now).total_seconds() / 60
            if mins > 0:
                upcoming.append((mins, jcd, rno, dl))
    upcoming.sort()
    print('本日の会場', venues, '/ これからのレース', len(upcoming))

    targets = [u for u in upcoming
               if u[0] <= WINDOW_MIN and (u[1], u[2]) not in done]
    if manual:
        info = [f'会場 {len(venues)}場 / これからのレース {len(upcoming)}',
                '、'.join(VENUES.get(j, str(j)) for j in venues)]
        if upcoming:
            m, j, r, dl = upcoming[0]
            info.append(f'次の締切: {VENUES.get(j)} {r}R {dl:%H:%M}')
            if not targets:
                targets = [upcoming[0]]
        info.append(open(os.path.join(MODEL, 'trained_at.txt')).read()
                    if os.path.exists(os.path.join(MODEL, 'trained_at.txt'))
                    else '')
        notify('動作確認OK', '\n'.join(info))

    for mins, jcd, rno, dl in targets:
        try:
            run_race(bst, lk, jcd, rno, hd, dl, now)
        except Exception as e:
            print('エラー', jcd, rno, e)


if __name__ == '__main__':
    main()
