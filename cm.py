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
URLS.update({2: 'toda-xml', 3: 'edogawa-reporter', 4: 'heiwajima-table', 7: 'gamagori-js', 12: 'suminoe-table',
             15: 'marugame-table', 16: 'kojima-table', 17: 'miyajima-fragments'})
URLS.update({
    1: 'http://www.kiryu-kyotei.com/modules/yosou/syussou.php?day={d}&race={r}',
    18: 'https://www.boatrace-tokuyama.jp/modules/yosou/syussou.php?day={d}&race={r}',
    22: 'https://www.boatrace-fukuoka.com/modules/yosou/syussou.php?day={d}&race={r}',
})
GROUP_SITES = {
    6: 'https://www.boatrace-hamanako.jp', 8: 'https://www.boatrace-tokoname.jp',
    10: 'https://www.boatrace-mikuni.jp', 13: 'https://boatrace-amagasaki.jp',
    14: 'https://www.n14.jp', 20: 'https://www.wmb.jp', 21: 'https://www.boatrace-ashiya.com',
}
URLS.update({v: site+'/modules/yosou/group-syussou.php?day={d}&race={r}'
             for v, site in GROUP_SITES.items()})
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
    # Improvement is positive; 良くなった must not match the prefix 良くな.
    for w in ['良くなった', '良くなって', 'よくなった', 'よくなって', '上向いて']:
        p += t.count(w)
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


def parse_table(html, allow_empty=False):
    """Read an explicit boat / player / comment table, including rowspan.

    Commentary and prediction text outside a player-comment column is excluded.
    """
    import lxml.html
    from ot.parse import table_grid
    if not html:
        return {}
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return {}
    boat_ids = {}
    for tbl in doc.xpath('//table'):
        trs = tbl.xpath('./tr|./thead/tr|./tbody/tr')
        if trs and '枠' in trs[0].text_content() and not trs[0].xpath('./th'):
            for cell in trs[0].xpath('./td'):
                cell.tag = 'th'
        grid, nr = table_grid(tbl)
        if not grid:
            continue
        nc = max(c for _, c in grid) + 1
        labels = {c: ''.join(re.sub(r'\s+', '', grid[r, c][0])
                            for r in range(nr) if (r, c) in grid and grid[r, c][1])
                  for c in range(nc)}
        def find(words):
            return next((c for c, text in labels.items() if any(w in text for w in words)), None)
        lane, player, comment = find(['枠', '艇番']), find(['選手名', 'レーサー名', '登録番号', '登番']), find(['選手コメント', '記者コメント', '特注情報'])
        if lane is None:
            continue
        if player is not None:
            for r in range(nr):
                a, b = grid.get((r, lane)), grid.get((r, player))
                if a and b and not a[1] and re.fullmatch('[1-6]', a[0].strip()):
                    reg = re.search(r'(?<!\d)(\d{4})(?!\d)', b[0])
                    if reg:
                        boat_ids.setdefault(int(a[0].strip()), int(reg[1]))
        if comment is None:
            # Gama labels the current quote column simply コメント.
            comment = next((c for c, text in labels.items() if text == 'コメント'), None)
        if comment is None:
            continue
        out = {}
        for r in range(nr):
            a, c = grid.get((r, lane)), grid.get((r, comment))
            b = grid.get((r, player)) if player is not None else None
            if not a or not c or a[1]:
                continue
            m = re.fullmatch(r'[1-6]', a[0].strip())
            reg_match = re.search(r'(?<!\d)(\d{4})(?!\d)', b[0]) if b else None
            reg = int(reg_match[1]) if reg_match else boat_ids.get(int(m[0])) if m else None
            text = c[0].split('過去コメント')[0].split('選手のコメント履歴')[0].strip()
            if m and reg and (text or allow_empty) and int(m[0]) not in out:
                out[int(m[0])] = (int(reg), text)
        if out:
            return out
    return {}


def parse_fukuoka(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    out, boat, reg = {}, None, None
    for tr in soup.select('table.syussou > tr, table.syussou > tbody > tr'):
        cell, player = tr.select_one('td.tei'), tr.select_one('td.racer')
        if cell is not None:
            text = cell.get_text(strip=True)
            boat = int(text) if text.isdigit() and 1 <= int(text) <= 6 else None
            match = re.search(r'(?<!\d)(\d{4})(?!\d)', player.get_text(' ', strip=True)) if player else None
            reg = int(match[1]) if match else None
        comment = tr.select_one('td.comment dd')
        if boat and reg and comment is not None:
            out[boat] = (reg, comment.get_text(' ', strip=True))
    return out


def parse_edogawa(card_html, comment_html):
    """Official reporter 特注情報, explicitly separate from racer quotations."""
    from bs4 import BeautifulSoup
    card, comments = BeautifulSoup(card_html, 'html.parser'), BeautifulSoup(comment_html, 'html.parser')
    registered = {}
    clean = lambda s: re.sub(r'\s+', '', s)
    for tr in card.select('tr'):
        boat = tr.find('td', class_=re.compile(r'^bg_tei_a[1-6]$'))
        link = tr.find('a', href=re.compile(r'toban=\d{4}'))
        if boat and link:
            number = int(re.search(r'([1-6])$', boat['class'][0])[1])
            registered[number] = (int(re.search(r'toban=(\d{4})', link['href'])[1]),
                                  clean(link.get_text()))
    out = {}
    for tr in comments.select('tr'):
        cells = [tr.select_one(f'td.item-col{i}') for i in (2, 3, 4)]
        if any(c is None for c in cells):
            continue
        boat = cells[0].get_text(strip=True)
        if not boat.isdigit() or int(boat) not in registered:
            continue
        reg, name = registered[int(boat)]
        if name == clean(cells[1].get_text()):
            out[int(boat)] = (reg, '[記者特注情報] '+cells[2].get_text(' ', strip=True))
    return out


def parse_gamagori_js(text):
    """Parse string literals; never execute downloaded JavaScript."""
    body = text.split('function funcToDayComment', 1)[-1].split('return strComment', 1)[0]
    return {int(reg): comment.replace("\\'", "'")
            for reg, comment in re.findall(
                r"strTouban\s*===?\s*'(\d{4})'\)\s*\{\s*strComment\s*=\s*'((?:\\.|[^'\\])*)'", body)}


def fetch(get, jcd, rno, hd, rl_html=''):
    """予想用 → {艇番: dict(cm_pos, cm_neg, cm_score, cm_mot, text)}"""
    rows = {}
    if jcd == 3:
        from venue_data import decode
        card = get(f'https://www.boatrace-edogawa.com/modules/yosou/syussou.php?day={hd}&race={rno}', tries=1)
        comment = get(f'https://www.boatrace-edogawa.com/sp/index.php?page=yosou-syussou&day={hd}&race={rno}', tries=1)
        entries = parse_edogawa(decode(card), decode(comment)) if card is not None and comment is not None else {}
        for b, (reg, text) in entries.items():
            p, n = score(text)
            rows[b] = dict(reg=reg, text=text, cm_pos=p, cm_neg=n, cm_score=p-n, cm_mot=np.nan)
    elif jcd == 2:
        from venue_data import decode
        import xml.etree.ElementTree as ET
        response = get(f'https://www.boatrace-toda.jp/xml/kaisai/{hd}/race_table_comment_{rno:02d}.xml', tries=1)
        if response is not None:
            try:
                root = ET.fromstring(decode(response))
                for record in root.findall('.//record'):
                    boat, reg = int(record.findtext('teiban')), int(record.findtext('toban'))
                    text = record.findtext('comment') or ''
                    if 1 <= boat <= 6 and text.strip():
                        p, n = score(text)
                        rows[boat] = dict(reg=reg, text=text, cm_pos=p, cm_neg=n, cm_score=p-n, cm_mot=np.nan)
            except (ET.ParseError, ValueError, TypeError):
                pass
    elif jcd in (4, 7, 12, 15, 16, 17):
        from venue_data import html as venue_html, decode
        if jcd == 7:
            url = ('https://www.gamagori-kyotei.com/asp/gamagori/kyogi/kyogihtml/'
                   f'comment/comment{hd}07{rno:02d}.htm')
            card = get(url, tries=1)
            js = get('https://www.gamagori-kyotei.com/asp/gamagori/kyogi/kyogihtml/'
                     f'js/comment{hd}07.js', tries=1)
            entries = parse_table(decode(card), allow_empty=True) if card is not None else {}
            comments = parse_gamagori_js(decode(js)) if js is not None else {}
            entries = {b: (reg, comments[reg]) for b, (reg, _) in entries.items() if reg in comments}
        else:
            text = venue_html(get, jcd, rno, hd)
            if jcd == 17:
                fragments = text.split('####')
                text = fragments[4] if len(fragments) > 4 else ''
            entries = parse_table(text)
        for b, (reg, comment) in entries.items():
            p, n = score(comment)
            rows[b] = dict(reg=reg, text=comment, cm_pos=p, cm_neg=n,
                           cm_score=p - n, cm_mot=np.nan)
    elif jcd in (1, 18, 22) or jcd in GROUP_SITES:
        from venue_data import decode
        response = get(URLS[jcd].format(d=hd, r=rno), tries=1)
        text = decode(response) if response is not None else ''
        entries = parse_fukuoka(text) if jcd == 22 else parse_table(text)
        for b, (reg, comment) in entries.items():
            p, n = score(comment)
            rows[b] = dict(reg=reg, text=comment, cm_pos=p, cm_neg=n,
                           cm_score=p-n, cm_mot=np.nan)
    elif jcd in URLS:
        r = get(URLS[jcd].format(d=hd, r=rno), tries=1)
        if r is None or not r.content:
            return {}
        for b, (reg, c) in parse_race(r.content.decode('utf-8', errors='replace')).items():
            p, n = score(c)
            rows[b] = dict(reg=reg, text=c, cm_pos=p, cm_neg=n, cm_score=p - n, cm_mot=np.nan)
    elif jcd == 24:
        from common import now_jst
        if str(hd) != now_jst().strftime('%Y%m%d'):
            return {}  # This list is current-day only.
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
    if not rows:
        import json
        from common import MODEL
        from venue_data import decode
        path = os.path.join(MODEL, 'source_registry.json')
        registry = json.load(open(path)) if os.path.exists(path) else {}
        for template in registry.get(str(jcd), {}).get('comment_urls', [])[:1]:
            response = get(template.format(d=hd, r=rno), tries=1)
            if response is None:
                continue
            for b, (reg, text) in parse_table(decode(response)).items():
                p, n = score(text)
                rows[b] = dict(reg=reg, text=text, cm_pos=p, cm_neg=n,
                               cm_score=p-n, cm_mot=np.nan)
    return rows


def add_rel(d, keys=None):
    if keys:
        d['cm_d'] = d['cm_score'] - d.groupby(keys)['cm_score'].transform('mean')
    else:
        d['cm_d'] = d['cm_score'] - d['cm_score'].mean()
    return d


COLS = ['hd', 'jcd', 'rno', 'boat', 'racer', 'cm_pos', 'cm_neg', 'cm_score', 'cm_mot', 'text', 'observed_at', 'deadline', 'asof_verified']


def append(root, hd, jcd, rno, rows, deadline=None):
    if not rows:
        return
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, f'comment_{hd}.csv')
    from common import now_jst
    observed = now_jst()
    verified = int(deadline is not None and observed < deadline)
    if os.path.exists(p):
        old = pd.read_csv(p, dtype=str)
        if list(old.columns) != COLS:
            old.reindex(columns=COLS).to_csv(p, index=False)
    t = pd.DataFrame([dict(observed_at=observed.isoformat(),
                           deadline=deadline.isoformat() if deadline else '',
                           asof_verified=verified, hd=hd, jcd=jcd, rno=rno, boat=b, racer=v['reg'],
                           **{k: v[k] for k in ['cm_pos', 'cm_neg', 'cm_score', 'cm_mot', 'text']})
                      for b, v in sorted(rows.items())])[COLS]
    t.to_csv(p, mode='a', header=not os.path.exists(p), index=False)


def load_hist(roots):
    import glob
    fs = [f for r in roots for f in glob.glob(os.path.join(r, 'comment_*.csv'))]
    if not fs:
        return pd.DataFrame(columns=COLS)
    d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    return d[d['hd'].astype(str) != 'hd']


def add_hist(df, roots):
    h = load_hist(roots)
    # A retrospective page can include comments updated after this race.
    # Use only captures proven to precede the corresponding deadline.
    if len(h):
        verified = pd.to_numeric(h.get('asof_verified', pd.Series(0, index=h.index)), errors='coerce')
        h = h[verified == 1].drop_duplicates(['hd', 'jcd', 'rno', 'boat'], keep='last')
    if len(h):
        # Re-score raw text whenever the dictionary changes.
        if 'text' in h:
            scores = [score(text) for text in h['text']]
            h['cm_pos'] = [x[0] for x in scores]
            h['cm_neg'] = [x[1] for x in scores]
            h['cm_score'] = h['cm_pos'] - h['cm_neg']
        h = h.rename(columns={'hd': 'date', 'jcd': 'venue', 'rno': 'race'})
        h['date'] = h['date'].astype(int)
        k = ['date', 'venue', 'race', 'boat', 'racer']
        df = df.merge(h[k + ['cm_pos', 'cm_neg', 'cm_score', 'cm_mot']], on=k, how='left')
    else:
        for c in ['cm_pos', 'cm_neg', 'cm_score', 'cm_mot']:
            df[c] = np.nan
    return add_rel(df, ['date', 'venue', 'race'])
