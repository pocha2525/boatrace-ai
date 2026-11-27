"""直前情報（展示ST・チルト・部品交換など）の過去分を、少しずつ取りためる。

公式サイトの直前情報ページを、前日から過去にさかのぼって1レースずつ読む。
1回に読むページ数は BF_BUDGET（中継の1日の上限を使い切らないため）。
保存先：hist/before/before_日付.csv（1日分そろったら hist/before/done.txt に日付を書く）
"""
import os
import datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import before
import cm
from common import get_direct
from common import page, now_jst, k_path, read_k
import train

ROOT = os.path.join('hist', 'before')
DONE = os.path.join(ROOT, 'done.txt')
BUDGET = int(os.environ.get('BF_BUDGET', '3000'))


def complete_races(path):
    """6艇の展示ST・タイムがそろったレースだけ取得済みとする。"""
    if not os.path.exists(path):
        return set()
    h = pd.read_csv(path).drop_duplicates(['jcd', 'rno', 'boat'], keep='last')
    if not {'ex_st', 'tenji'} <= set(h.columns):
        return set()
    out = set()
    for key, g in h.groupby(['jcd', 'rno']):
        if set(g['boat']) == set(range(1, 7)) and g[['ex_st', 'tenji']].notna().all().all():
            out.add(tuple(map(int, key)))
    return out


def eligible_races(path):
    """Only six-boat races with recorded courses can train strict inference."""
    rows, races = read_k(path)
    if not rows:
        return []
    entries = pd.DataFrame(rows)
    valid = set()
    for key, group in entries.groupby(['venue', 'race']):
        if set(group.boat) == set(range(1, 7)) and group.course.notna().all():
            valid.add(key)
    return [race for race in races if (race['venue'], race['race']) in valid]


def done_days():
    # 過去の「試行済み」を完了とした記録も、保存データから再確認する。
    if not os.path.exists(DONE):
        return set()
    out = set()
    for hd in open(DONE).read().split():
        d = datetime.datetime.strptime(hd, '%Y%m%d').date()
        if not os.path.exists(k_path(d)):
            continue
        races = eligible_races(k_path(d))
        expected = {(r['venue'], r['race']) for r in races}
        if expected and expected <= complete_races(before.path(ROOT, hd)):
            out.add(hd)
    return out


def one(args):
    hd, jcd, rno = args
    try:
        return before.parse(page('beforeinfo', jcd, rno, hd), hd, jcd, rno)
    except Exception as e:  # 1レース読めなくても続ける
        print('読めず', hd, jcd, rno, e)
        return []


def main(budget=BUDGET):
    os.makedirs(ROOT, exist_ok=True)
    done = done_days()
    used = 0
    d = now_jst().date() - datetime.timedelta(days=1)
    got_days = []
    while used < budget and d >= train.START:
        hd = f'{d:%Y%m%d}'
        if hd in done or not os.path.exists(k_path(d)):
            d -= datetime.timedelta(days=1)
            continue
        races = eligible_races(k_path(d))
        p = before.path(ROOT, hd)
        have = complete_races(p)
        todo = [(hd, r['venue'], r['race']) for r in races
                if (r['venue'], r['race']) not in have]
        todo = todo[:budget - used]
        with ThreadPoolExecutor(4) as ex:
            res = list(ex.map(one, todo))
        used += len(todo)
        rows = [x for r in res for x in r]
        before.append(ROOT, rows)
        ok = sum(1 for r in res if r)
        print(hd, '読んだ', len(todo), '取れた', ok)
        expected = {(r['venue'], r['race']) for r in races}
        successful = complete_races(p)
        if expected and expected <= have | successful:
            with open(DONE, 'a') as f:
                f.write(hd + '\n')
            got_days.append(hd)
        d -= datetime.timedelta(days=1)
    return used, got_days


CM_ROOT = os.path.join('hist', 'comment')
CM_DONE = os.path.join(CM_ROOT, 'done.txt')
CM_BUDGET = int(os.environ.get('CM_BUDGET', '800'))


def comments(budget=CM_BUDGET):
    """下関・唐津の選手コメントの過去分を取りためる（各場のサイトを直接、間を空けて読む）"""
    import time
    os.makedirs(CM_ROOT, exist_ok=True)
    done = set(open(CM_DONE).read().split()) if os.path.exists(CM_DONE) else set()
    used = 0
    d = now_jst().date() - datetime.timedelta(days=1)
    while used < budget and d >= datetime.date(2024, 1, 1):
        hd = f'{d:%Y%m%d}'
        if not os.path.exists(k_path(d)):
            d -= datetime.timedelta(days=1)
            continue
        entries, races = read_k(k_path(d))
        registered = {(row['venue'], row['race'], row['boat']): row['racer'] for row in entries}
        for v in cm.URLS:
            from venue_data import STATIC
            if v in STATIC:
                continue  # Static current-day pages cannot supply historical comments.
            key = f'{hd}-{v}'
            rs = sorted(r['race'] for r in races if r['venue'] == v)
            if key in done or not rs:
                continue
            got = 0
            for rno in rs:
                if used >= budget:
                    break
                try:
                    rows = cm.fetch(get_direct, v, rno, hd)
                except Exception as e:
                    print('読めず', key, rno, e)
                    rows = {}
                used += 1
                rows = {boat: row for boat, row in rows.items()
                        if registered.get((v, rno, boat)) == row['reg']}
                got += len(rows) == 6
                cm.append(CM_ROOT, hd, v, rno, rows)
                time.sleep(1.0)
            if got == len(rs):
                with open(CM_DONE, 'a') as f:
                    f.write(key + '\n')
            print('コメント', key, got, '/', len(rs))
        d -= datetime.timedelta(days=1)
    return used


def coverage():
    """取りためた日数"""
    return len(done_days())


if __name__ == '__main__':
    print(main())
