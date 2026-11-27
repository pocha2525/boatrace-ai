"""オリジナル展示（一周・まわり足・直線）。各レース場の公式サイトのページから読む。

過去分：hassak0408/boat-oriten が集めている表（data/場番号/年.csv）を使う。
予想用：締切前に、その場の公式サイトのページを中継（GAS）経由で読む。
場によって測る区間が違うので、同じレースの中での差と順位だけを材料にする。
"""
import os
import glob

import numpy as np
import pandas as pd

OT = ['ot_lap_d', 'ot_turn_d', 'ot_str_d', 'ot_lap_rk', 'ot_turn_rk', 'ot_str_rk']
SRC = 'https://github.com/hassak0408/boat-oriten'

URLS = {
    1: 'http://www.kiryu-kyotei.com/modules/yosou/cyokuzen.php?day={d}&race={r}',
    2: 'https://www.boatrace-toda.jp/xml/kaisai/{d}/race_table_original_{r2}.xml',
    5: 'https://boatrace-tamagawa.com/modules/yosou/oriten.php?day={d}&race={r}',
    6: 'https://www.boatrace-hamanako.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    8: 'https://www.boatrace-tokoname.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    10: 'https://www.boatrace-mikuni.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    11: 'https://www.boatrace-biwako.jp/modules/yosou/cyokuzen.php?day={d}&race={r}&kind=2',
    13: 'https://boatrace-amagasaki.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    14: 'https://www.n14.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    18: 'https://www.boatrace-tokuyama.jp/modules/yosou/tenji.php?day={d}&race={r}',
    19: 'https://www.boatrace-shimonoseki.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    20: 'https://www.wmb.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    21: 'https://www.boatrace-ashiya.com/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
    22: 'https://www.boatrace-fukuoka.com/modules/yosou/tenji_info.php?day={d}&race={r}',
    23: 'https://www.boatrace-karatsu.jp/modules/yosou/group-cyokuzen.php?day={d}&race={r}&kind=2',
}


def add_rel(d, keys=None):
    """レース内の平均との差と順位（タイムは小さいほど良いので、順位1が一番速い）"""
    g = d.groupby(keys) if keys else None
    for src, dd, rk in [('ot_lap', 'ot_lap_d', 'ot_lap_rk'),
                        ('ot_turn', 'ot_turn_d', 'ot_turn_rk'),
                        ('ot_str', 'ot_str_d', 'ot_str_rk')]:
        if g is not None:
            d[dd] = d[src] - g[src].transform('mean')
            d[rk] = g[src].rank()
        else:
            d[dd] = d[src] - d[src].mean()
            d[rk] = d[src].rank()
    return d


def load_hist(root):
    """boat-oriten の data/ を読む → date, venue, race, boat, ot_lap, ot_turn, ot_str"""
    fs = glob.glob(os.path.join(root, 'data', '*', '*.csv'))
    fs = [f for f in fs if os.path.basename(f)[:4].isdigit()]
    if not fs:
        return pd.DataFrame()
    t = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    t['date'] = t['date'].astype(str).str.replace('-', '').astype(int)
    t = t.rename(columns={'lane': 'boat', 'lap': 'ot_lap', 'turn': 'ot_turn',
                          'straight': 'ot_str'})
    return t[['date', 'venue', 'race', 'boat', 'ot_lap', 'ot_turn', 'ot_str']] \
        .drop_duplicates(['date', 'venue', 'race', 'boat'], keep='last')


def add_hist(df, root):
    h = load_hist(root)
    k = ['date', 'venue', 'race', 'boat']
    if len(h):
        df = df.merge(h, on=k, how='left')
    else:
        for c in ['ot_lap', 'ot_turn', 'ot_str']:
            df[c] = np.nan
    return add_rel(df, ['date', 'venue', 'race'])


def fetch(get, jcd, rno, hd):
    """予想用：その場の公式サイトから読む → {艇番: (一周, まわり足, 直線)}。出していない場は {}"""
    u = URLS.get(jcd)
    if not u:
        return {}
    r = get(u.format(d=hd, r=rno, r2=f'{rno:02d}'), tries=1)
    if r is None or not r.content:
        return {}
    raw = r.content
    try:
        html = raw.decode('utf-8')
    except UnicodeDecodeError:
        html = raw.decode('cp932', errors='replace')
    from .parse import parse_page
    rows, _ = parse_page(html)
    return {x['lane']: (x.get('lap'), x.get('turn'), x.get('straight')) for x in rows}
