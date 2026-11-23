"""締切が近いレースを予想し、期待値の高い買い目をスマホに通知する。"""
import os
import re
import csv
import datetime
from itertools import permutations

import pandas as pd
import lightgbm as lgb
from bs4 import BeautifulSoup

import numpy as np

from common import (MODEL, LOGS, JST, VENUES, FEATS, X, TARGETS, BR,
                    now_jst, get, page, notify, add_rel)

WINDOW_MIN = 13      # 締切まで何分以内のレースを予想するか
# 本命モード：本命がはっきりしたレースで、当たりやすい3連単を最大2点
# （2点目は期待値1.0以上のときだけ）
EV_TH = 1.0          # 一番当たりやすい買い目の期待値がこれ以上
MIN_PROB = 0.12      # 一番当たりやすい買い目の確率がこれ以上
MAX_BETS = 2         # 1レースあたりの最大点数
LAST = {}  # 直前に予想したレースの進入・確率（通知の表示用）
LOG_COLS = ['hd', 'jcd', 'venue', 'rno', 'deadline', 'pick',
            'prob', 'odds', 'ev', 'run_at']


# ---------- 読み込み ----------

def load_model():
    paths = [os.path.join(MODEL, f'model_{y}.txt') for y in TARGETS]
    if not all(os.path.exists(p) for p in paths):
        return None, None
    bst = [lgb.Booster(model_file=p) for p in paths]
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
    nums = [int(x.get_text(strip=True))
            for x in s.select('span.table1_boatImage1Number')
            if x.get_text(strip=True).isdigit()]
    # スタート展示の並び（1コースから順）。6艇そろわなければ「読み取れず」
    crs = {b: i + 1 for i, b in enumerate(nums[:6])}
    if sorted(crs) != [1, 2, 3, 4, 5, 6]:
        crs = {}
    w = re.findall(r'LabelData">\s*([\d.]+)\s*(m|cm)\s*<', b)
    wind = next((float(v) for v, u in w if u == 'm'), 0.0)
    wave = next((float(v) for v, u in w if u == 'cm'), 0.0)
    rl = page('racelist', jcd, rno, hd)
    rl_ids = list(dict.fromkeys(re.findall(r'toban=(\d{4})', rl)))[:6]
    r = BeautifulSoup(rl, 'html.parser')
    h2 = r.select('td.is-lineH2')
    n = len(h2) // 6
    mot = []
    for i in range(6):
        try:
            txt = h2[i * n + 3].get_text(' ', strip=True)
            mot.append(int(txt.split()[0]))
        except (IndexError, ValueError):
            mot.append(None)
    return ids, tj, crs, wind, wave, mot, rl_ids


def check_race(ids, tj, crs, mot, rl_ids, strict):
    """6艇の対応が確かめられなければ、見送りの理由を返す"""
    if len(ids) != 6 or len(set(ids)) != 6:
        return f'選手が6人そろわない（{len(ids)}人）'
    if len(rl_ids) == 6 and ids != rl_ids:
        return '出走表と直前情報で選手の並びが一致しない'
    if len(mot) != 6 or any(m is None for m in mot):
        return 'モーター番号が6艇分そろわない'
    if strict:
        if len(tj) != 6 or any(t is None or t != t for t in tj):
            return '展示タイムが6艇分そろわない（欠場・展示前の可能性）'
        if not crs:
            return '進入が読み取れない'
    return None


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

def predict(bst, lk, jcd, rno, hd, strict=True):
    """各艇の 1着・2着・3着 の確率を返す: (艇番, w, q2, q3)
    strict=True（締切前）：展示タイム・進入が6艇分そろわなければ見送り
    strict=False（朝の候補）：進入は枠なりとして暫定で計算"""
    ids, tj, crs, wind, wave, mot, rl_ids = race_info(jcd, rno, hd)
    reason = check_race(ids, tj, crs, mot, rl_ids, strict)
    LAST['skip'] = reason
    if reason:
        return None
    yr = int(hd[:4])

    def g(n, *k):
        return lk[n].get(tuple(k))

    rows = []
    for i, rid in enumerate(ids):
        rid = int(rid)
        b = i + 1
        c = crs.get(b, b)
        r = {'boat': b, 'course': c, 'venue': jcd,
             'tenji': tj[i] if i < len(tj) else None,
             'wind': wind, 'wave': wave,
             'm_top2': g('m_top2', jcd, mot[i], yr)}
        for _, _, n in FEATS:
            if n.startswith('rc_'):
                r[n] = g(n, rid, c)
            elif n.startswith('rv_'):
                r[n] = g(n, rid, jcd)
            elif n.startswith('vc_'):
                r[n] = g(n, jcd, c)
            elif n.startswith('r_'):
                r[n] = g(n, rid)
        rows.append(r)
    d = add_rel(pd.DataFrame(rows).astype(float))
    one = d.loc[d['course'] == 1, 'rc_win_d']
    d['in_d'] = one.iloc[0] if len(one) else np.nan
    w, t2, t3 = [m.predict(d[m.feature_name()]) for m in bst]
    w = w / w.sum()
    t2 = t2 * 2 / t2.sum()
    t3 = t3 * 3 / t3.sum()
    q2 = np.clip(t2 - w, 1e-3, None)
    q3 = np.clip(t3 - t2, 1e-3, None)
    boats = d['boat'].astype(int).tolist()
    LAST.update(crs=crs, boats=boats, w=w, q2=q2 / q2.sum(),
                q3=q3 / q3.sum(),
                rc=d[['rc_top2_d', 'rc_top3_d']].values.tolist(),
                course=d['course'].astype(int).tolist())
    return boats, w, q2, q3


def tri_probs(pr):
    """3連単すべての確率（1着・2着・3着それぞれの確率で計算）"""
    boats, w, q2, q3 = pr
    out = {}
    for a, b, c in permutations(range(len(boats)), 3):
        s2 = q2.sum() - q2[a]
        s3 = q3.sum() - q3[a] - q3[b]
        out[f'{boats[a]}-{boats[b]}-{boats[c]}'] = \
            w[a] * q2[b] / s2 * q3[c] / s3
    return out


def combos(pr, om):
    res = []
    for k, q in tri_probs(pr).items():
        o = om.get(k)
        if o == o and o and q >= 0.01:
            res.append((q * o, k, q, o))
    # 当たりやすい順（確率の高い順）に並べる
    return sorted(res, key=lambda x: -x[2])


def entry_text():
    crs = LAST.get('crs') or {}
    if not crs:
        return '進入: 読み取れず'
    order = [b for b, c in sorted(crs.items(), key=lambda x: x[1])]
    mark = '' if order == [1, 2, 3, 4, 5, 6] else '  ※枠なりではない'
    return '進入: ' + '-'.join(map(str, order)) + mark


def rank_text(label, key):
    v = LAST[key]
    top = sorted(zip(LAST['boats'], v), key=lambda x: -x[1])[:3]
    return f'{label}: ' + ' / '.join(f'{b}号艇{p * 100:.0f}%' for b, p in top)


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
    pr = predict(bst, lk, jcd, rno, hd)
    base = {'hd': hd, 'jcd': jcd, 'venue': name, 'rno': rno,
            'deadline': f'{dl:%H:%M}', 'run_at': f'{now:%H:%M}'}
    if pr is None:
        print(name, rno, 'R 見送り：', LAST.get('skip') or '情報が取れない')
        return
    res = combos(pr, odds_map(jcd, rno, hd))  # 当たりやすい順
    # 一番当たりやすい組み合わせが条件を満たすレースだけ買う。
    # 2点目以降も期待値1.0以上のときだけ追加する
    ok = res and res[0][2] >= MIN_PROB and res[0][0] >= EV_TH
    bets = [res[0]] if ok else []
    if ok:
        bets += [x for x in res[1:MAX_BETS] if x[0] >= EV_TH]
    rows = []
    for ev, k, q, o in bets:
        rows.append(dict(base, pick=k, prob=round(q, 4), odds=o,
                         ev=round(ev, 2)))
    if not rows:
        rows.append(dict(base, pick='', prob='', odds='', ev=''))
    write_log(hd, rows)

    head = rank_text('1着', 'w')
    if bets:
        lines = [f'締切 {dl:%H:%M}', entry_text(), head,
                 rank_text('2着', 'q2'), rank_text('3着', 'q3'), '本命:']
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
        ta = os.path.join(MODEL, 'trained_at.txt')
        info.append('モデル: ' + (open(ta).read().strip()
                    if os.path.exists(ta) else 'v2 3モデル(11/23 Colab)'))
        notify('動作確認OK', '\n'.join(info))

    for mins, jcd, rno, dl in targets:
        try:
            run_race(bst, lk, jcd, rno, hd, dl, now)
        except Exception as e:
            print('エラー', jcd, rno, e)


if __name__ == '__main__':
    main()
