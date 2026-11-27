"""朝の候補レース通知：出走表だけで本命がはっきりしていそうなレースを一覧にする。

展示タイム・進入・直前オッズはまだ無いので、
コースは枠なり、展示タイムと風・波は「不明」として計算する。
最終判断は締切前の通知（predict.py）で行う。
"""
import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

import predict
import time

from common import (VENUES, LOGS, now_jst, page, send_notify, SENT_OK,
                    SENT_FAIL, SENT_UNKNOWN)

MIN_PROB = predict.MIN_PROB   # 本命の3連単がこの確率以上なら候補
MAX_LINES = 60
AHEAD_MIN = 10   # 締切までこの分数より短いレースは、候補に入れない（間に合わないため）


def info_morning(jcd, rno, hd):
    """出走表から、選手とモーターだけを読む（predict.race_info と同じ形で返す）"""
    html = page('racelist', jcd, rno, hd)
    predict.LAST['html_racelist'] = html
    predict.LAST['html_before'] = ''
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
    # 出走表の数字（級別・勝率など）を予想に渡す。直前情報は朝はまだ無い
    predict.HTML[(jcd, rno)] = {'before': '', 'racelist': html}
    return ids, [nan] * 6, {}, nan, nan, mot, ids


def top_combos(pr, k=2):
    t = predict.tri_probs(pr)
    return sorted(((q, c) for c, q in t.items()), reverse=True)[:k]


def send(title, message):
    """送信失敗のときだけ最大3回まで送り直す（結果不明は送り直さない）"""
    for i in range(3):
        st = send_notify(title, message, notification_id='morning-' +
                         (os.environ.get('HD') or f'{now_jst():%Y%m%d}'))['status']
        if st != SENT_FAIL:
            return st
        time.sleep(5 * (i + 1))
    return st


def save_candidates(hd, found):
    """朝の候補を logs/morning_日付.csv に保存（締切前に見送りも通知するため）"""
    os.makedirs(LOGS, exist_ok=True)
    with open(predict.morning_path(hd), 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=predict.MORNING_COLS)
        w.writeheader()
        for dl, jcd, rno, top in found:
            w.writerow(dict(hd=hd, jcd=jcd, venue=VENUES.get(jcd, jcd),
                            rno=rno, deadline=f'{dl:%H:%M}',
                            picks=' '.join(k for q, k in top),
                            probs=' '.join(f'{q:.4f}' for q, k in top)))


def main():
    hd = os.environ.get('HD') or f'{now_jst():%Y%m%d}'
    manual = os.environ.get('MANUAL') == '1'
    # 自動実行で、今日の候補がもう保存済みなら二重に送らない
    if os.path.exists(predict.morning_path(hd)) and not manual:
        print('今日の朝の候補は送信済みです')
        return
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
    # 締切を過ぎたレース・もうすぐ締切のレースは計算しない
    start = now_jst()
    races = [r for r in races
             if (r[0] - start).total_seconds() / 60 >= AHEAD_MIN]
    print('これからのレース数', len(races))

    def one(r):
        dl, jcd, rno = r
        try:
            pr = predict.predict(bst, lk, jcd, rno, hd, strict=False)
        except Exception as e:
            print('エラー', jcd, rno, e)
            return None
        if not pr:
            return None
        top = top_combos(pr)
        if top[0][0] < MIN_PROB:
            return None
        return dl, jcd, rno, top

    with ThreadPoolExecutor(4) as ex:
        found = [x for x in ex.map(one, races) if x]
    # 計算に時間がかかるので、送る直前にもう一度、締切を過ぎたものを外す
    sent_at = now_jst()
    found = [x for x in found if x[0] > sent_at]
    found.sort()

    if not found:
        st = send('本日の本命候補', 'これからのレースに、本命がはっきりしたレースはありませんでした')
        if st in (SENT_OK, SENT_UNKNOWN):
            save_candidates(hd, [])
        return
    lines = []
    for dl, jcd, rno, top in found[:MAX_LINES]:
        picks = ' '.join(f'{k}({q * 100:.0f}%)' for q, k in top)
        lines.append(f'{dl:%H:%M} {VENUES.get(jcd, jcd)}{rno}R  {picks}')
    lines.append('')
    lines.append('※展示・オッズ前の候補です（進入は枠なりと仮定）。最終判断は締切前の通知で。')
    lines.append('※候補のレースは、締切前に「買い」か「見送り」のどちらかを通知します。')
    st = send(f'本日の本命候補 {len(found)}レース', '\n'.join(lines))
    # 受付成功・結果不明のときに保存（届いた可能性があるので二重に送らない）
    if st in (SENT_OK, SENT_UNKNOWN):
        save_candidates(hd, found)
    print('送信結果', st)


if __name__ == '__main__':
    main()
