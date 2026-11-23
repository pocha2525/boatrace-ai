"""共通の設定と関数（データ取得・成績ファイルの読み取り・通知）"""
import os
import re
import io
import base64
import time
import datetime
import tempfile
import unicodedata

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get('DATA_DIR') or os.path.join(ROOT, 'data')
MODEL = os.environ.get('MODEL_DIR') or os.path.join(ROOT, 'model')
LOGS = os.environ.get('LOGS_DIR') or os.path.join(ROOT, 'logs')
JST = datetime.timezone(datetime.timedelta(hours=9))

UA = {'User-Agent': 'Mozilla/5.0 (personal boatrace-ai)'}
BR = 'https://www.boatrace.jp/owpc/pc/race/'
MB = 'https://www1.mbrace.or.jp/od2/'

VENUES = {
    1: '桐生', 2: '戸田', 3: '江戸川', 4: '平和島', 5: '多摩川', 6: '浜名湖',
    7: '蒲郡', 8: '常滑', 9: '津', 10: '三国', 11: 'びわこ', 12: '住之江',
    13: '尼崎', 14: '鳴門', 15: '丸亀', 16: '児島', 17: '宮島', 18: '徳山',
    19: '下関', 20: '若松', 21: '芦屋', 22: '福岡', 23: '唐津', 24: '大村',
}

# (集計に使う列, 対象の値, 特徴量の名前)
FEATS = [
    (['racer'], 'win', 'r_win'),
    (['racer'], 'top2', 'r_top2'),
    (['racer'], 'st_ok', 'r_st'),
    (['racer', 'course'], 'win', 'rc_win'),
    (['racer', 'course'], 'top2', 'rc_top2'),
    (['racer', 'venue'], 'win', 'rv_win'),
    (['venue', 'motor', 'year'], 'top2', 'm_top2'),
    (['venue', 'course'], 'win', 'vc_win'),
]

X = ['boat', 'course', 'venue', 'tenji', 'tenji_diff',
     'tenji_rank', 'r_win', 'r_top2', 'r_st', 'rst_diff',
     'rc_win', 'rc_top2', 'rv_win', 'm_top2', 'vc_win',
     'rwin_rank', 'wind', 'wave']


def now_jst():
    return datetime.datetime.now(JST)


RELAY = os.environ.get('RELAY_URL', '').strip()


class Resp:
    """中継（GAS）から受け取った中身を requests の返り値と同じ形にする"""
    def __init__(self, content):
        self.content = content
        self.status_code = 200
        self.encoding = 'utf-8'

    @property
    def text(self):
        return self.content.decode(self.encoding or 'utf-8', errors='replace')


def get_relay(url):
    r = requests.get(RELAY, params={'u': url}, timeout=90)
    if r.status_code != 200:
        print('中継エラー', r.status_code, url)
        return 'retry'
    j = r.json()
    st = j.get('status')
    if st == 200:
        return Resp(base64.b64decode(j.get('body', '')))
    if st == 404:
        return None
    print('中継', j.get('error') or st, url)
    return 'retry'


def get(url, tries=3):
    """URLを取得する。404ならNone、失敗時は数回やり直す。
    RELAY_URL があれば、Google Apps Script 経由で取得する。"""
    for i in range(tries):
        try:
            if RELAY:
                r = get_relay(url)
                if r != 'retry':
                    return r
            else:
                r = requests.get(url, headers=UA, timeout=30)
                if r.status_code == 200:
                    return r
                if r.status_code == 404:
                    return None
        except (requests.RequestException, ValueError) as e:
            print('通信エラー', url, e)
        time.sleep(2 * (i + 1))
    return None


def page(kind, jcd, rno, hd):
    """boatrace.jp のレースページ（HTML）を取得する。"""
    r = get(f'{BR}{kind}?rno={rno}&jcd={jcd:02d}&hd={hd}')
    if r is None:
        return ''
    r.encoding = 'utf-8'
    return r.text


# ---------- 競走成績（Kファイル） ----------

def k_path(d):
    return os.path.join(DATA, 'K', f'K{d:%y%m%d}.TXT')


def download_k(d):
    """指定日の競走成績ファイルを取得して data/K に保存する。"""
    path = k_path(d)
    if os.path.exists(path):
        return path
    r = get(f'{MB}K/{d:%Y%m}/k{d:%y%m%d}.lzh')
    if r is None or not r.content:
        return None
    import lhafile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.lzh', delete=False) as tmp:
        tmp.write(r.content)
        tmp_name = tmp.name
    try:
        f = lhafile.Lhafile(tmp_name)
        names = f.namelist()
        if not names:
            return None
        with open(path, 'wb') as out:
            out.write(f.read(names[0]))
    finally:
        os.remove(tmp_name)
    return path


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


ROW_PAT = re.compile(
    r'^\s*(\S{1,2})\s+([1-6])\s+(\d{4})\s+([^\d\s].*?)'
    r'\s+(\d{1,3})\s+(\d{1,3})\s+(.*)$')


def head_info(line, date, venue, race):
    info = {'date': date, 'venue': venue, 'race': race}
    w = re.search(r'(晴|曇り|曇|雨|雪|霧)', line)
    info['weather'] = w.group(1) if w else None
    wd = re.search(r'風\s*(\S+)\s+(\d+)m', line)
    info['wind_dir'] = wd.group(1) if wd else None
    info['wind'] = num(wd.group(2)) if wd else None
    wv = re.search(r'波\s*(\d+)cm', line)
    info['wave'] = num(wv.group(1)) if wv else None
    info['tri'] = None
    info['tri_pay'] = None
    return info


def parse_k_text(text, date):
    """成績ファイルの中身を、艇ごとの行とレースごとの行に分ける。"""
    venue = None
    race = None
    races = {}
    rows = []
    for raw in text.splitlines():
        line = unicodedata.normalize('NFKC', raw)
        m = re.match(r'^(\d{2})KBGN', line.strip())
        if m:
            venue = int(m.group(1))
            race = None
            continue
        m = re.match(r'^\s*(\d{1,2})R\s', line)
        if m and not re.search(r'\d-\d', line):
            race = int(m.group(1))
            races[(venue, race)] = head_info(line, date, venue, race)
            continue
        if race is None:
            continue
        m = re.search(r'3連単\s+(\d-\d-\d)\s+(\d+)', line)
        if m:
            races[(venue, race)]['tri'] = m.group(1)
            races[(venue, race)]['tri_pay'] = int(m.group(2))
            continue
        m = ROW_PAT.match(line)
        if m:
            rest = m.group(7).split() + [None, None, None]
            g1 = m.group(1)
            rows.append({
                'date': date, 'venue': venue, 'race': race,
                'rank': int(g1) if g1.isdigit() else None,
                'rank_raw': g1,
                'boat': int(m.group(2)),
                'racer': int(m.group(3)),
                'motor': int(m.group(5)),
                'tenji': num(rest[0]),
                'course': num(rest[1]),
                'st': num(rest[2]),
            })
    return rows, list(races.values())


def read_k(path):
    date = int('20' + os.path.basename(path)[1:7])
    with open(path, encoding='cp932', errors='ignore') as f:
        return parse_k_text(f.read(), date)


# ---------- 通知 ----------

def notify(title, message):
    """ntfy でスマホに通知する（NTFY_TOPIC が無ければ表示だけ）。"""
    print('=====', title)
    print(message)
    topic = os.environ.get('NTFY_TOPIC', '').strip()
    if not topic:
        print('(NTFY_TOPIC が未設定のため通知は送っていません)')
        return
    try:
        requests.post('https://ntfy.sh/', timeout=20, json={
            'topic': topic, 'title': title, 'message': message})
    except requests.RequestException as e:
        print('通知エラー', e)
