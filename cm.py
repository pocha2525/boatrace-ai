"""選手コメント（各レース場の公式サイト）を、言葉の良し悪しで点数にする。

下関・唐津：直前情報の「選手コメント」タブ（レースごと、前日・直前のコメント）
大村：「全選手コメント・モーター評価一覧」（その日の全選手。モーター評価1〜7点つき）
材料：cm_pos（良い言葉の数）、cm_neg（悪い言葉の数）、cm_score（差）、
      cm_d（レース内の平均との差）、cm_mot（大村のモーター評価）
"""
import os
import re

import numpy as np
import pandas as pd

CM = ['cm_pos', 'cm_neg', 'cm_score', 'cm_d', 'cm_mot']
URLS = {
    19: 'https://www.boatrace-shimonoseki.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=3',
    23: 'https://www.boatrace-karatsu.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=3',
}
OMURA = 'https://www.omurakyotei.jp/yosou/comment.php'

# 打ち消しを含む言い回し（先に置きかえる）
FLIP_POS = ['悪いところはな', '悪くない', '悪くなさ', '悪い感じではな', '悪い感じはな', '下がることはな',
            '下がりはしな', '下がる感じはな', '下がらな', '問題な', '負けていな', '負けてな',
            '重くな', '重たくはな', '重くはな', '不安はな', '心配な', 'やられる感じもなかった']
FLIP_NEG = ['良くな', 'よくな', '良くはな', 'いいという感じはしな', 'いい感じはしな', 'いいとは言えな', '良いとは言えな', '出ていな',
            '出てない', '出ているような感じではな', '伸びな', '合っていな', '合ってな', '合わな',
            '乗れな', '回れな', 'そこまでではな', '感じはしなかった', 'わけではな']
POS = ['良い', 'いい', '良か', '良さ', '良く', 'よか', '出ている', '出てる', '強め', '強い', '上位',
       '上積み', '伸びる', '伸びて', '合っている', '合ってる', '合った', 'バランス', '乗りやすい',
       '自信', '好感触', '力強', 'しっかり', 'まあまあ', '上々', '十分']
NEG = ['悪い', '悪か', '弱い', '弱め', '劣勢', '下がる', '下がって', '重い', '重た', '難しい', '乗りづら',
       '乗りにく', '足りな', '欲しい', '不安', '気になる', '失敗', '厳しい', '苦しい', 'イマイチ', 'いまいち',
       '差がある', 'もう少し', '分が悪い', 'ひどい', '違和感', 'ダメ', 'だめ', 'しんどい', '物足りな', '課題']


# 良し悪しを言っていない言い回し（数えない）
NEUTRAL = ['良くしたい', '良くなれば', 'よくしたい', '良くなるように', '良いところを探', 'いいところを探']


def score(text):
    """コメント → (良い言葉の数, 悪い言葉の数)"""
    if not isinstance(text, str) or not text.strip():
        return np.nan, np.nan
    t = text
    p = n = 0
    for w in NEUTRAL:
        t = t.replace(w, '＃')
    for w in FLIP_POS:
        p += t.count(w)
        t = t.replace(w, '＃')
    for w in FLIP_NEG:
        n += t.count(w)
        t = t.replace(w, '＃')
    p += sum(t.count(w) for w in POS)
    n += sum(t.count(w) for w in NEG)
    return float(p), float(n)


def _vis(html):
    h = re.sub(r'<script.*?</script>|<style.*?</style>|<!--.*?-->', '', html, flags=re.S)
    t = re.sub(r'<[^>]+>', '|', h)
    t = t.replace('&nbsp;', ' ')
    t = re.sub(r'\s+', ' ', t)
    return re.sub(r'(\| ?)+', '|', t)


ROW = re.compile(r'\|([1-6])\|(?:[AB][12]\|/)?(\d{4})\|([^|]+)\|([^|]+)\|([^|]*)\|\3選手のコメント履歴')


def parse_race(html):
    """下関・唐津のレースページ → {艇番: (登録番号, コメント)}"""
    t = _vis(html)
    out = {}
    ms = list(ROW.finditer(t))
    for i, m in enumerate(ms):
        boat, reg, _, _, c = m.groups()
        # 直前のコメント（「…（13：09現在）」）があれば足す
        end = ms[i + 1].start() if i + 1 < len(ms) else len(t)
        seg = t[m.end():end]
        now = re.findall(r'\|([^|]*現在）)', seg)
        out[int(boat)] = (int(reg), (c + ' ' + ' '.join(now)).strip())
    return out


def parse_omura(html):
    """大村の一覧 → {名前（空白なし）: (コメント, モーター評価)}"""
    t = _vis(html)
    i = t.find('モーター評価は')
    t = t[i:] if i >= 0 else t
    out = {}
    for m in re.finditer(r'\|([^|]{2,12})\|([^|]*?)\|(?:([^|]*?\[\d+:\d+\][^|]*)\|)?(\d)点\|', t):
        name, c, now, pt = m.groups()
        out[re.sub(r'[\s　]', '', name)] = ((c + ' ' + (now or '')).strip(), float(pt))
    return out


def names_from_racelist(html):
    """出走表 → {登録番号: 名前（空白なし）}"""
    t = _vis(html)
    out = {}
    for m in re.finditer(r'(\d{4}) / \|(?:A1|A2|B1|B2)\|([^|]+)\|', t):
        out[int(m.group(1))] = re.sub(r'[\s　]', '', m.group(2))
    return out


_OMURA = {}


def fetch(get, jcd, rno, hd, rl_html=''):
    """予想用 → {艇番: dict(cm_pos, cm_neg, cm_score, cm_mot, text)}"""
    rows = {}
    if jcd in URLS:
        r = get(URLS[jcd].format(d=hd, r=rno), tries=1)
        if r is None or not r.content:
            return {}
        for b, (reg, c) in parse_race(r.content.decode('utf-8', errors='replace')).items():
            p, n = score(c)
            rows[b] = dict(reg=reg, text=c, cm_pos=p, cm_neg=n, cm_score=p - n, cm_mot=np.nan)
    elif jcd == 24:
        if hd not in _OMURA:
            r = get(OMURA, tries=1)
            _OMURA[hd] = parse_omura(r.content.decode('utf-8', errors='replace')) if r is not None else {}
        nm = names_from_racelist(rl_html)
        ids = list(nm)
        for i, reg in enumerate(ids[:6]):
            x = _OMURA[hd].get(nm[reg])
            if x:
                p, n = score(x[0])
                rows[i + 1] = dict(reg=reg, text=x[0], cm_pos=p, cm_neg=n, cm_score=p - n, cm_mot=x[1])
    return rows


def add_rel(d, keys=None):
    if keys:
        d['cm_d'] = d['cm_score'] - d.groupby(keys)['cm_score'].transform('mean')
    else:
        d['cm_d'] = d['cm_score'] - d['cm_score'].mean()
    return d


COLS = ['hd', 'jcd', 'rno', 'boat', 'racer', 'cm_pos', 'cm_neg', 'cm_score', 'cm_mot', 'text']


def append(root, hd, jcd, rno, rows):
    if not rows:
        return
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, f'comment_{hd}.csv')
    t = pd.DataFrame([dict(hd=hd, jcd=jcd, rno=rno, boat=b, racer=v['reg'],
                           **{k: v[k] for k in ['cm_pos', 'cm_neg', 'cm_score', 'cm_mot', 'text']})
                      for b, v in sorted(rows.items())])[COLS]
    t.to_csv(p, mode='a', header=not os.path.exists(p), index=False)


def load_hist(roots):
    import glob
    fs = [f for r in roots for f in glob.glob(os.path.join(r, 'comment_*.csv'))]
    if not fs:
        return pd.DataFrame(columns=COLS)
    d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    return d.drop_duplicates(['hd', 'jcd', 'rno', 'boat'], keep='last')


def add_hist(df, roots):
    h = load_hist(roots)
    if len(h):
        h = h.rename(columns={'hd': 'date', 'jcd': 'venue', 'rno': 'race'})
        h['date'] = h['date'].astype(int)
        k = ['date', 'venue', 'race', 'boat']
        df = df.merge(h[k + ['cm_pos', 'cm_neg', 'cm_score', 'cm_mot']], on=k, how='left')
    else:
        for c in ['cm_pos', 'cm_neg', 'cm_score', 'cm_mot']:
            df[c] = np.nan
    return add_rel(df, ['date', 'venue', 'race'])
