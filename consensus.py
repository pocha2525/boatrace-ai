"""A conservative shared signal; never manufacture blended probabilities."""
import datetime
import glob
import json
import math
import os

from common import SENT_OK, SENT_UNKNOWN
from reasoned_notice import labels

VERSION = 'consensus-v1'
MIN_PROB = .12
MIN_EV = 1.0
MAX_BETS = 2  # Live limit; increase only after prospective return validation.
AUTHORIZED_MAX_BETS = 4


def select(ranked, record, max_bets=MAX_BETS):
    """Intersect model top four with the already validated odds-free forecast."""
    if not isinstance(max_bets, int) or not 1 <= max_bets <= AUTHORIZED_MAX_BETS:
        raise ValueError('Authorized comparison range is one to four picks')
    if not isinstance(record, dict) or record.get('eligible') is not True:
        return []
    picks = record.get('picks', [])
    if not isinstance(picks, list) or len(picks) != len(set(picks)) or len(picks) != 4:
        return []
    ordered = sorted(ranked, key=lambda x: -x[2])[:4]
    return [x for x in ordered if x[1] in picks and
            all(math.isfinite(float(v)) for v in (x[0], x[2], x[3])) and
            x[2] >= MIN_PROB and x[0] >= MIN_EV][:max_bets]


def daily_id(hd):
    return f'{VERSION}-{hd}'


def day_used(logs, hd):
    """Include durable pending/unknown receipts, even if final CSV save failed."""
    import csv
    path = os.path.join(logs, f'notify_{hd}.csv')
    if os.path.exists(path):
        with open(path, encoding='utf8', newline='') as f:
            if any(r.get('policy') == VERSION and r.get('status') in
                   (SENT_OK, SENT_UNKNOWN, '送信予約') for r in csv.DictReader(f)):
                return True
    for path in glob.glob(os.path.join(logs, 'delivery', '*.json')):
        try:
            with open(path, encoding='utf8') as f:
                row = json.load(f)
            if row.get('notification_id') == daily_id(hd) and row.get('status') not in ('送信失敗', '締切により中止'):
                return True
        except (OSError, ValueError):
            continue
    return False


def evidence(record):
    head = str(record.get('head', ''))
    groups = record.get('group_evidence', {})
    strengths = [g for g, values in groups.items() if values.get(head, 0) >= .6]
    lines = ['根拠：AIと能力・今節・展示などの判断ルール、双方の候補に含まれる買い目です。']
    if strengths:
        lines.append('1着候補の比較上位：' + labels(strengths[:3]))
    try:
        fixed = datetime.datetime.fromisoformat(record['observed_at'])
        lines.append(f'判断固定 {fixed:%H:%M:%S}（オッズ取得前）')
    except (KeyError, ValueError, TypeError):
        pass
    lines.append('※確率・EVはAI単独の推定値です。統合予想の回収改善は検証中です。')
    return lines
