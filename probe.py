"""各レース場の公式サイトを調べる：選手コメント・整備・ペラ・オリジナル展示がどのページにあるか。
見つかったページを logs/samples/venues/ に、調べた結果を logs/samples/venues/report.json に置く。"""
import os, re, json, datetime
from urllib.parse import urljoin, urlparse
import requests
from common import UA, now_jst, download_k, read_k

ERR = {}


class _R:
    def __init__(self, c):
        self.content = c


def get(url, tries=1):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        ERR[url] = r.status_code
        return _R(r.content) if r.status_code == 200 else None
    except requests.RequestException as e:
        ERR[url] = repr(e)[:150]
        return None

SITES = {
    1: 'http://www.kiryu-kyotei.com', 2: 'https://www.boatrace-toda.jp',
    3: 'https://www.boatrace-edogawa.com', 4: 'https://www.heiwajima.gr.jp',
    5: 'https://boatrace-tamagawa.com', 6: 'https://www.boatrace-hamanako.jp',
    7: 'https://www.gamagori-kyotei.com', 8: 'https://www.boatrace-tokoname.jp',
    9: 'https://www.boatrace-tsu.com', 10: 'https://www.boatrace-mikuni.jp',
    11: 'https://www.boatrace-biwako.jp', 12: 'https://www.boatrace-suminoe.jp',
    13: 'https://boatrace-amagasaki.jp', 14: 'https://www.n14.jp',
    15: 'https://www.marugameboat.jp', 16: 'https://www.kojimaboat.jp',
    17: 'https://www.boatrace-miyajima.com', 18: 'https://www.boatrace-tokuyama.jp',
    19: 'https://www.boatrace-shimonoseki.jp', 20: 'https://www.wmb.jp',
    21: 'https://www.boatrace-ashiya.com', 22: 'https://www.boatrace-fukuoka.com',
    23: 'https://www.boatrace-karatsu.jp', 24: 'https://www.omurakyotei.jp',
}
PATHS = [
    '/modules/yosou/cyokuzen.php?day={d}&race={r}',
    '/modules/yosou/cyokuzen.php?day={d}&race={r}&kind=1',
    '/modules/yosou/cyokuzen.php?day={d}&race={r}&kind=2',
    '/modules/yosou/cyokuzen.php?day={d}&race={r}&kind=3',
    '/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=1',
    '/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    '/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=3',
    '/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=4',
    '/modules/yosou/comment.php?day={d}&race={r}',
    '/modules/yosou/group-comment.php?day={d}&race={r}',
    '/modules/yosou/syusou.php?day={d}&race={r}',
    '/modules/yosou/group-syusou.php?day={d}&race={r}',
    '/modules/yosou/zenken.php?day={d}',
    '/modules/yosou/group-zenken.php?day={d}',
    '/modules/yosou/oriten.php?day={d}&race={r}',
    '/modules/yosou/group-oriten.php?day={d}&race={r}',
    '/modules/yosou/tenji.php?day={d}&race={r}',
    '/modules/yosou/tenji_info.php?day={d}&race={r}',
    '/modules/yosou/seibi.php?day={d}',
    '/modules/yosou/group-seibi.php?day={d}',
]
KEYW = ('yosou', 'tenji', 'oriten', 'cyokuzen', 'chokuzen', 'comment', 'zenken',
        'seibi', 'syusou', 'race', 'data', 'original', 'before', 'yoso')
OUT = 'logs/samples/venues'


def text(r):
    if r is None or not r.content:
        return ''
    try:
        return r.content.decode('utf-8')
    except UnicodeDecodeError:
        return r.content.decode('cp932', errors='replace')


def marks(h):
    return {k: (k in h) for k in ['コメント', 'まわり', '回り足', '一周', '直線', '整備', 'ペラ', 'プロペラ', '部品', '前検']}


def main():
    os.makedirs(OUT, exist_ok=True)
    today = now_jst().date()
    # 各場の、最近レースがあった日（成績ファイルから）
    last = {}
    for i in range(1, 6):
        d = today - datetime.timedelta(days=i)
        p = download_k(d)
        if not p:
            continue
        _, races = read_k(p)
        for r in races:
            last.setdefault(r['venue'], f'{d:%Y%m%d}')
    rep = {}
    for v, site in SITES.items():
        d = last.get(v)
        res = dict(site=site, date=d, hits=[], tried=[], links=[])
        n = 0
        if d:
            for pth in PATHS:
                url = site + pth.format(d=d, r=6)
                h = text(get(url, tries=1))
                mk = marks(h)
                res['tried'].append(dict(url=url, size=len(h), code=ERR.get(url), **{k: v_ for k, v_ in mk.items() if v_}))
                if h and (mk['コメント'] or mk['まわり'] or mk['回り足'] or mk['整備']) and n < 6:
                    fn = f'{v:02d}_{n}.html'
                    open(os.path.join(OUT, fn), 'w', encoding='utf-8').write(h)
                    res['hits'].append(dict(url=url, file=fn, **{k: v_ for k, v_ in mk.items() if v_}))
                    n += 1
        # トップページのリンクもたどる
        top = text(get(site + '/', tries=1))
        links = []
        for hh in re.findall(r'(?:href|src)="([^"#]+)"', top):
            full = urljoin(site + '/', hh)
            if urlparse(full).netloc.replace('www.', '') == urlparse(site).netloc.replace('www.', '') \
                    and any(k in full.lower() for k in KEYW) and full not in links:
                links.append(full)
        res['top'] = ERR.get(site + '/')
        res['links'] = links[:80]
        for u in links[:25]:
            h = text(get(u, tries=1))
            mk = marks(h)
            if h and (mk['コメント'] or mk['まわり'] or mk['回り足'] or mk['整備']) and n < 10:
                fn = f'{v:02d}_{n}.html'
                open(os.path.join(OUT, fn), 'w', encoding='utf-8').write(h)
                res['hits'].append(dict(url=u, file=fn, **{k: v_ for k, v_ in mk.items() if v_}))
                n += 1
        rep[v] = res
        print(v, d, len(res['hits']), [x['url'] for x in res['hits']][:4], flush=True)
    rep['_at'] = str(now_jst())
    json.dump(rep, open(os.path.join(OUT, 'report.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
