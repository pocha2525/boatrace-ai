"""朝の候補レース通知：出走表だけで本命がはっきりしていそうなレースを一覧にする。

展示タイム・進入・直前オッズはまだ無いので、
コースは枠なり、展示タイムと風・波は「不明」として計算する。
最終判断は締切前の通知（predict.py）で行う。
"""
import os
import re
from itertools import permutations
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

import predict
from common import VENUES, now_jst, page, notify

MIN_PROB = predict.MIN_PROB   # 本命の3連単がこの確率以上なら候補
MAX_LINES = 60


def info_morning(jcd, rno, hd):
    """出走表から、選手とモーターだけを読む（predict.race_info と同じ形で返す）"""
    html = page('racelist', jcd, rno, hd)
    ids = list(dict.fromkeys(re.findall(r'toban=(\d{4})', html)))[:6]
    s = BeautifulSoup(html, 'html.parser')
    h2 = s.select('td.is-lineH2')
    n = len(h2) // 6
    mot = []
    for i in range(6):
        try:
            mot.append(int(h2[i * n + 3].get_text(' ', strip=True).split()[0]))
        except (IndexError, ValueError):
            mot.append(None)
    nan = float('nan')
    return ids, [nan] * 6, {}, nan, nan, mot


def top_combos(p, k=2):
    res = []
    for a, b, c in permutations(p, 3):
        q = p[a] * p[b] / (1 - p[a])
        q = q * p[c] / max(1 - p[a] - p[b], 0.01)
        res.append((q, f'{a}-{b}-{c}'))
    return sorted(res, reverse=True)[:k]


def main():
    hd = os.environ.get('HD') or f'{now_jst():%Y%m%d}'
    bst, lk = predict.load_model()
    if bst is None:
        print('モデルがありません')
        return
    predict.race_info = info_morning  # 朝は出走表だけで予想する

    races = []
    for jcd in predict.today_venues(hd):
        for rno, dl in predict.deadlines(jcd, hd).items():
            races.append((dl, jcd, rno))
    print('本日のレース数', len(races))

    def one(r):
        dl, jcd, rno = r
        try:
            p = predict.predict(bst, lk, jcd, rno, hd)
        except Exception as e:
            print('エラー', jcd, rno, e)
            return None
        if not p:
            return None
        top = top_combos(p)
        if top[0][0] < MIN_PROB:
            return None
        return dl, jcd, rno, top

    with ThreadPoolExecutor(4) as ex:
        found = [x for x in ex.map(one, races) if x]
    found.sort()

    if not found:
        notify('本日の本命候補', '本命がはっきりしたレースはありませんでした')
        return
    lines = []
    for dl, jcd, rno, top in found[:MAX_LINES]:
        picks = ' '.join(f'{k}({q * 100:.0f}%)' for q, k in top
                         if q >= MIN_PROB)
        lines.append(f'{dl:%H:%M} {VENUES.get(jcd, jcd)}{rno}R  {picks}')
    lines.append('')
    lines.append('※展示・オッズ前の候補です。最終判断は締切前の通知で。')
    notify(f'本日の本命候補 {len(found)}レース', '\n'.join(lines))


if __name__ == '__main__':
    main()
