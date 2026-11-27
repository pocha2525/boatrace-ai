"""締切が近いレースを予想し、期待値の高い買い目をスマホに通知する。"""
import os
import re
import csv
import datetime
import threading
from functools import wraps
from itertools import permutations

import pandas as pd
import lightgbm as lgb
from bs4 import BeautifulSoup

import numpy as np

import setsu
import extra
import bfile
import before
import ot
import cm

from common import (MODEL, LOGS, JST, VENUES, FEATS, X, TARGETS, BR,
                    now_jst, get, get_direct, page, notify, add_rel, send_notify,
                    SENT_OK, SENT_FAIL, SENT_UNKNOWN, SENT_NONE, ThreadState)

WINDOW_MIN = 15      # 締切まで何分以内のレースを予想するか（11/24 13→15）
# 本命モード：本命がはっきりしたレースで、当たりやすい3連単を最大2点
# （2点目は期待値1.0以上のときだけ）
EV_TH = 1.0          # 一番当たりやすい買い目の期待値がこれ以上
EV_TH2 = 0.8         # 2点目（確率2位）の期待値がこれ以上なら追加（11/25 1.0→0.8）
MIN_PROB = 0.12      # 一番当たりやすい買い目の確率がこれ以上
MAX_BETS = 2         # 1レースあたりの最大点数
LAST = ThreadState()  # レース処理の状態はスレッドごとに分離
HTML = ThreadState()  # Claude修正のレース別ページも保持
LOG_COLS = ['hd', 'jcd', 'venue', 'rno', 'deadline', 'pick',
            'prob', 'odds', 'ev', 'run_at']

# ---- 通知の安全対策 ----
SAFE_MIN = 2  # 締切まで残りこの分数未満なら送らない（締切後の通知も防ぐ）
ST_CANCEL = '締切により中止'
ST_SKIP = '条件未達'
# この状態の記録があるレースは、二度と通知しない
#  （送信失敗だけは、締切に間に合えば次の回に再試行する）
FINAL = {SENT_OK, SENT_UNKNOWN, SENT_NONE, ST_CANCEL, ST_SKIP}
NOTIFY_COLS = ['notify_id', 'hd', 'jcd', 'venue', 'rno', 'deadline',
               'run_start', 'decided_at', 'remain_decided',
               'send_start', 'send_end', 'remain_send', 'status',
               'http', 'ntfy_id', 'error', 'picks', 'probs', 'odds',
               'evs', 'reason', 'morning', 'notice']
# 朝の候補（morning.py が保存）
MORNING_COLS = ['hd', 'jcd', 'venue', 'rno', 'deadline', 'picks', 'probs']
# 次の起動（約10分後）では締切2分前を切るので、今回が最後の判定になる残り分数
LAST_CHANCE_MIN = 12


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
    # 今節の成績表（夜間の改善で毎朝作り直す。無ければ「初日」扱い）
    lk['_setsu'] = setsu.load_tables(MODEL)
    # フライング歴（夜間の改善で毎朝作り直す）
    lk['_f'] = extra.load_f(MODEL)
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
    wind = next((float(v) for v, u in w if u == 'm'), float('nan'))
    wave = next((float(v) for v, u in w if u == 'cm'), float('nan'))
    LAST['html_before'] = b
    HTML[(jcd, rno)] = {'before': b}
    rl = page('racelist', jcd, rno, hd)
    LAST['html_racelist'] = rl
    HTML[(jcd, rno)]['racelist'] = rl
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
    LAST.clear()
    ids, tj, crs, wind, wave, mot, rl_ids = race_info(jcd, rno, hd)
    reason = check_race(ids, tj, crs, mot, rl_ids, strict)
    LAST['skip'] = reason
    if reason:
        return None
    save_entries(hd, jcd, rno, ids)
    done = extra.today_results(LOGS, hd)   # 今日すでに終わったレースの着
    yr = int(hd[:4])
    pages = HTML.get((jcd, rno), {})   # このレースのページだけを使う（他のレースと混ざらない）
    try:
        rl = bfile.parse_racelist(pages.get('racelist') or '')
    except Exception as e:
        print('出走表の数字を読めない', e)
        rl = {}
    try:
        bf = before.parse(pages.get('before') or '', hd, jcd, rno) if strict else []
        if strict:
            before.append(LOGS, bf)   # 直前情報を記録（あとで学習に使う）
    except Exception as e:
        print('直前情報を読めない', e)
        bf = []
    try:
        otd = ot.fetch(get_direct, jcd, rno, hd) if strict else {}   # オリジナル展示（一周・まわり足・直線）
        if strict and otd:
            append_rows(os.path.join(LOGS, f'oriten_{hd}.csv'),
                        ['hd', 'jcd', 'rno', 'boat', 'lap', 'turn', 'straight'],
                        [dict(hd=hd, jcd=jcd, rno=rno, boat=k, lap=v[0], turn=v[1],
                              straight=v[2]) for k, v in sorted(otd.items())])
    except Exception as e:
        print('オリジナル展示を読めない', e)
        ot.LAST['memo'] = f'エラー {e!r}'
        otd = {}
    try:
        if strict and jcd in ot.URLS:
            append_rows(os.path.join(LOGS, f'oriten_check_{hd}.csv'),
                        ['hd', 'jcd', 'rno', 'n', 'size', 'memo'],
                        [dict(hd=hd, jcd=jcd, rno=rno, n=len(otd),
                              size=ot.LAST.get('size', ''), memo=ot.LAST.get('memo', ''))])
            if not otd and ot.LAST.get('html'):
                save_sample(hd, f'oriten_{jcd:02d}', ot.LAST['html'])
    except Exception as e:
        print('記録できない', e)
    try:
        cmd = cm.fetch(get_direct, jcd, rno, hd, pages.get('racelist') or '') if strict else {}
        if strict:
            cm.append(LOGS, hd, jcd, rno, cmd)
    except Exception as e:
        print('選手コメントを読めない', e)
        cmd = {}
    wxy = (np.nan, np.nan)
    if bf:
        nm = bfile.img_dir(jcd, bf[0].get('wind_img'))
        if nm is not None:
            wxy = bfile.wind_xy(nm, bf[0].get('wind'))

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
        # 今節の材料（今節の材料を使うモデルのときだけ予想に使われる）
        r.update(setsu.lookup(lk.get('_setsu', ({}, {})), rid, jcd, c,
                              r['tenji'], r.get('vc_win'), hd))
        # フライング歴・当日の前走（その材料を使うモデルのときだけ予想に使われる）
        r.update(extra.lookup(lk.get('_f', {}), done, rid, jcd, rno, hd))
        # 出走表の数字（級別・全国/当地の勝率・モーター/ボートの2連率など）
        r.update(rl.get(rid, {c: np.nan for c in bfile.BCOLS}))
        # 直前情報（体重・チルト・部品交換・展示ST など）
        r.update({k: (bf[i].get(k) if i < len(bf) else np.nan) for k in
                  ['bf_wt', 'bf_tilt', 'bf_prop', 'bf_parts', 'bf_adj',
                   'ex_st', 'ex_f', 'stab']})
        r['wd_x'], r['wd_y'] = wxy
        r['ot_lap'], r['ot_turn'], r['ot_str'] = otd.get(b, (np.nan, np.nan, np.nan))
        c_ = cmd.get(b) if cmd.get(b, {}).get('reg') == rid else None
        for k in ['cm_pos', 'cm_neg', 'cm_score', 'cm_mot']:
            r[k] = c_[k] if c_ else np.nan
        rows.append(r)
    d = add_rel(pd.DataFrame(rows).astype(float))
    d = bfile.add_brel(d)
    d = before.add_rel(d)
    d = ot.add_rel(d)
    d = cm.add_rel(d)
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


def read_log(path):
    """記録を読む。マージで見出し行が重複していても取り除く"""
    if not os.path.exists(path):
        return None
    t = pd.read_csv(path, dtype=str)
    key = 'hd' if 'hd' in t.columns else 'notify_id'
    t = t[t[key] != key]
    return t


def done_races(hd):
    """もう予想・通知しないレース（会場, R）"""
    done = set()
    t = read_log(log_path(hd))
    if t is not None:
        done |= {(int(j), int(r)) for j, r in zip(t['jcd'], t['rno'])}
    n = read_log(notify_path(hd))
    if n is not None:
        n = n[n['status'].isin(FINAL)]
        done |= {(int(j), int(r)) for j, r in zip(n['jcd'], n['rno'])}
    return done


def notify_path(hd):
    return os.path.join(LOGS, f'notify_{hd}.csv')


_LOG_LOCK = threading.RLock()


def locked_log(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _LOG_LOCK:
            return fn(*args, **kwargs)
    return wrapped


@locked_log
def append_rows(path, cols, rows):
    os.makedirs(LOGS, exist_ok=True)
    new = not os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        if new:
            w.writeheader()
        w.writerows(rows)


def write_log(hd, rows):
    """実運用の記録（成績の集計に使う）"""
    append_rows(log_path(hd), LOG_COLS, rows)


def write_notify(hd, row):
    """検証用の記録（通知の判断・送信結果・時刻・予測をすべて残す）"""
    append_rows(notify_path(hd), NOTIFY_COLS, [row])


def morning_path(hd):
    return os.path.join(LOGS, f'morning_{hd}.csv')


def morning_races(hd):
    """朝の候補に上がったレース（会場, R）"""
    t = read_log(morning_path(hd))
    if t is None:
        return set()
    return {(int(j), int(r)) for j, r in zip(t['jcd'], t['rno'])}


def skip_reason(res, om):
    """買わなかった理由（本番と同じ判定の順番）"""
    if not om:
        return 'オッズが取れない'
    if not res:
        return 'オッズのある買い目が無い'
    ev, k, q, o = res[0]
    if q < MIN_PROB:
        return f'本命{k}の確率{q * 100:.1f}%（{MIN_PROB * 100:.0f}%未満）'
    return f'本命{k}の期待値{ev:.2f}（{EV_TH:.1f}未満）'


def send_skip_notice(name, rno, dl, nid, reason, res):
    """朝の候補だったレースを見送ったことを通知する。送信結果の状態を返す"""
    if remain(dl) < SAFE_MIN:
        return '締切により送らず'
    lines = [f'締切 {dl:%H:%M}', f'理由: {reason}']
    if res:
        lines += [entry_text(), rank_text('1着', 'w'), '本命（買わない）:']
        for ev, k, q, o in res[:MAX_BETS]:
            lines.append(f'{k}  {o}倍  確率{q * 100:.1f}%  期待値{ev:.2f}')
    lines += ['※朝の候補でしたが、締切前の判定で見送りです', f'通知ID: {nid}']
    return send_notify(f'見送り {name} {rno}R', '\n'.join(lines))['status']


@locked_log
def save_entries(hd, jcd, rno, ids):
    """出走の記録（当日の前走結果を作るため）。1レース1回だけ書く"""
    p = os.path.join(LOGS, f'entries_{hd}.csv')
    if os.path.exists(p):
        t = pd.read_csv(p, dtype=str)
        if ((t['jcd'] == str(jcd)) & (t['rno'] == str(rno))).any():
            return
    append_rows(p, ['hd', 'jcd', 'rno', 'boat', 'racer'],
                [dict(hd=hd, jcd=jcd, rno=rno, boat=i + 1, racer=r)
                 for i, r in enumerate(ids)])


def save_sample(hd, kind, html):
    """公式ページの見本を1日1回だけ残す（新しい材料の読み取り方を確かめるため）"""
    if not html:
        return
    d = os.path.join(LOGS, 'samples')
    p = os.path.join(d, f'{hd}_{kind}.html')
    if os.path.exists(p):
        return
    os.makedirs(d, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(html)


def remain(dl):
    return (dl - now_jst()).total_seconds() / 60


def run_race(bst, lk, jcd, rno, hd, dl, now, cand=False):
    """cand=True: 朝の候補に上がったレース（見送りも通知する）"""
    name = VENUES.get(jcd, str(jcd))
    nid = f'{hd}-{jcd:02d}-{rno:02d}'  # 通知ID（1レース1通知）
    pr = predict(bst, lk, jcd, rno, hd)
    try:
        save_sample(hd, 'beforeinfo', LAST.get('html_before'))
        save_sample(hd, 'racelist', LAST.get('html_racelist'))
    except Exception as e:
        print('見本を保存できない', e)
    base = {'hd': hd, 'jcd': jcd, 'venue': name, 'rno': rno,
            'deadline': f'{dl:%H:%M}', 'run_at': f'{now:%H:%M}'}
    mark = '○' if cand else ''
    if pr is None:
        why = LAST.get('skip') or '情報が取れない'
        print(name, rno, 'R 見送り：', why)
        # 朝の候補で、今回が最後の判定なら「見送り」を通知して終える
        # （それ以外は今まで通り、次の回にもう一度判定する）
        if cand and remain(dl) < LAST_CHANCE_MIN:
            st = send_skip_notice(name, rno, dl, nid, why, [])
            write_notify(hd, dict(base, notify_id=nid,
                                  run_start=f'{now:%H:%M:%S}',
                                  decided_at=f'{now_jst():%H:%M:%S}',
                                  remain_decided=round(remain(dl), 2),
                                  status=ST_SKIP, reason=why,
                                  morning=mark, notice=st))
            write_log(hd, [dict(base, pick='', prob='', odds='', ev='')])
        return
    om = odds_map(jcd, rno, hd)
    res = combos(pr, om)  # 当たりやすい順
    # 一番当たりやすい組み合わせが条件を満たすレースだけ買う。
    # 2点目は期待値0.8以上のときだけ追加する（EV_TH2）
    ok = res and res[0][2] >= MIN_PROB and res[0][0] >= EV_TH
    bets = [res[0]] if ok else []
    if ok:
        bets += [x for x in res[1:MAX_BETS] if x[0] >= EV_TH2]

    # 検証用の記録の共通部分（買い・見送りに関係なく、確率の上位2つを残す。
    # 買った買い目は成績記録 logs/日付.csv に入る）
    shown = res[:MAX_BETS]
    rec = dict(base, notify_id=nid, run_start=f'{now:%H:%M:%S}',
               decided_at=f'{now_jst():%H:%M:%S}',
               remain_decided=round(remain(dl), 2),
               send_start='', send_end='', remain_send='',
               http='', ntfy_id='', error='',
               picks=' '.join(k for ev, k, q, o in shown),
               probs=' '.join(f'{q:.4f}' for ev, k, q, o in shown),
               odds=' '.join(str(o) for ev, k, q, o in shown),
               evs=' '.join(f'{ev:.2f}' for ev, k, q, o in shown),
               morning=mark, notice='')
    no_bet = dict(base, pick='', prob='', odds='', ev='')

    head = rank_text('1着', 'w')
    if not bets:
        why = skip_reason(res, om)
        st = send_skip_notice(name, rno, dl, nid, why, res) if cand else ''
        write_notify(hd, dict(rec, status=ST_SKIP, reason=why, notice=st))
        write_log(hd, [no_bet])
        print(f'{name} {rno}R 見送り（{why} / {head}）')
        return

    # 判定した時点で締切まで2分未満なら送らない
    if rec['remain_decided'] < SAFE_MIN:
        write_notify(hd, dict(rec, status=ST_CANCEL,
                              reason=f'判定時に締切まで{SAFE_MIN}分未満'))
        write_log(hd, [no_bet])
        print(f'{name} {rno}R 通知中止：締切まで{rec["remain_decided"]}分')
        return

    lines = [f'締切 {dl:%H:%M}', entry_text(), head,
             rank_text('2着', 'q2'), rank_text('3着', 'q3'), '買い目:']
    for ev, k, q, o in bets:
        lines.append(f'{k}  {o}倍  確率{q * 100:.1f}%  期待値{ev:.2f}')
    if cand:
        lines.append('※朝の候補のレースです')
    lines.append(f'通知ID: {nid}')

    # 送る直前にもう一度確かめる
    r_send = remain(dl)
    if r_send < SAFE_MIN:
        write_notify(hd, dict(rec, status=ST_CANCEL,
                              remain_send=round(r_send, 2),
                              reason=f'送信直前に締切まで{SAFE_MIN}分未満'))
        write_log(hd, [no_bet])
        print(f'{name} {rno}R 通知中止：締切まで{r_send:.2f}分')
        return
    t0 = now_jst()
    out = send_notify(f'買い {name} {rno}R', '\n'.join(lines))
    t1 = now_jst()
    rec.update(send_start=f'{t0:%H:%M:%S}', send_end=f'{t1:%H:%M:%S}',
               remain_send=round(r_send, 2), status=out['status'],
               http=out['http'], ntfy_id=out['ntfy_id'], error=out['error'])

    if out['status'] == SENT_OK:
        write_notify(hd, dict(rec, reason='ntfyが受付（スマホ到着は未確認）'))
        write_log(hd, [dict(base, pick=k, prob=round(q, 4), odds=o,
                            ev=round(ev, 2)) for ev, k, q, o in bets])
    elif out['status'] == SENT_FAIL:
        # 成績の記録には書かない → 次の回に締切まで余裕があれば再試行
        write_notify(hd, dict(rec, reason='送信失敗（次の回に再試行）'))
    elif out['status'] == SENT_UNKNOWN:
        # 届いた可能性があるので、次の回以降も再送しない
        write_notify(hd, dict(rec, reason='タイムアウト（再送しない）'))
        write_log(hd, [no_bet])
    else:
        write_notify(hd, dict(rec, reason='NTFY_TOPIC未設定'))
        write_log(hd, [no_bet])


# ---------- レース結果（その日のうちに記録する） ----------
RESULT_COLS = ['hd', 'jcd', 'venue', 'rno', 'deadline', 'tri', 'tri_pay',
               'got_at']
RESULT_WAIT_MIN = 12    # 締切からこの分数たったら結果を見に行く
RESULT_MAX = 8          # 1回の実行で見に行く最大レース数（時間をかけすぎない）
TRI_PAT = re.compile(r'3連単\s*([1-6])\s*-\s*([1-6])\s*-\s*([1-6])\s*'
                     r'[¥￥]\s*([\d,]+)')


def result_path(hd):
    return os.path.join(LOGS, f'results_{hd}.csv')


def parse_result(html):
    """公式のレース結果ページから3連単の組番と払戻金を読む。無ければ None"""
    if not html:
        return None
    text = re.sub(r'\s+', ' ', BeautifulSoup(html, 'html.parser').get_text(' '))
    m = TRI_PAT.search(text)
    if not m:
        return None
    return f'{m.group(1)}-{m.group(2)}-{m.group(3)}', int(m.group(4).replace(',', ''))


def update_results(hd, now):
    """判定したレースのうち、終わったものの結果を記録する（予想には影響しない）"""
    t = read_log(notify_path(hd))
    if t is None or len(t) == 0:
        return
    t = t.drop_duplicates('notify_id', keep='last')
    got = set()
    r = read_log(result_path(hd))
    if r is not None:
        r = r[r['tri'].str.fullmatch(r'[1-6]-[1-6]-[1-6]', na=False)]
        got = {(int(j), int(n)) for j, n in zip(r['jcd'], r['rno'])}
    todo = []
    for _, x in t.iterrows():
        key = (int(x['jcd']), int(x['rno']))
        if key in got:
            continue
        h, m = map(int, str(x['deadline']).split(':'))
        dl = now.replace(hour=h, minute=m, second=0, microsecond=0)
        past = (now - dl).total_seconds() / 60
        if past >= RESULT_WAIT_MIN:
            todo.append((past, key, x['venue'], x['deadline']))
    todo.sort(reverse=True)  # 古いレースから
    rows = []
    for past, (jcd, rno), venue, dl in todo[:RESULT_MAX]:
        try:
            html = page('raceresult', jcd, rno, hd)
            res = parse_result(html)
            if res:
                save_sample(hd, 'raceresult', html)
        except Exception as e:
            print('結果を読めない', jcd, rno, e)
            res = None
        if res is None:
            continue  # まだ出ていない。次の回にもう一度
        tri, pay = res
        rows.append(dict(hd=hd, jcd=jcd, venue=venue, rno=rno, deadline=dl,
                         tri=tri, tri_pay=pay, got_at=f'{now_jst():%H:%M}'))
    if rows:
        append_rows(result_path(hd), RESULT_COLS, rows)
        print('結果を記録', len(rows), 'レース')


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

    cands = morning_races(hd)
    print('朝の候補', len(cands), 'レース')
    for mins, jcd, rno, dl in targets:
        try:
            run_race(bst, lk, jcd, rno, hd, dl, now,
                     cand=(jcd, rno) in cands)
        except Exception as e:
            print('エラー', jcd, rno, e)

    # 判定が全部終わってから、終わったレースの結果を記録する
    try:
        update_results(hd, now_jst())
    except Exception as e:
        print('結果の記録に失敗', e)


if __name__ == '__main__':
    main()
