"""Readable notification text for the separately recorded reasoning forecast."""
import datetime
import math
import re

GROUP_NAMES = {
    'ability': '能力', 'form': '今節成績', 'machine': 'モーター・ボート',
    'foot': '展示の足', 'start': 'スタート', 'comment': '取得済みコメント',
    'course_head': '1着のコース適性',
}


def labels(groups):
    return '・'.join(GROUP_NAMES[g] for g in groups if g in GROUP_NAMES) or '差なし'


def lines(record, odds=None):
    """Format a verified saved forecast; never generate or change its picks."""
    title = '【私の判断ルール｜検証4点】'
    picks = record.get('picks', []) if isinstance(record, dict) else []
    good = (isinstance(record, dict) and record.get('eligible') is True
            and isinstance(picks, list) and all(isinstance(p, str) for p in picks)
            and len(picks) == len(set(picks)) == 4
            and all(re.fullmatch(r'[1-6]-[1-6]-[1-6]', p)
                    and len(set(p.split('-'))) == 3 for p in picks))
    if not good:
        return [title, '保留：必要データまたは締切前の記録が揃っていません。']
    out = [title]
    for pick in picks:
        try:
            value = float((odds or {}).get(pick, 0))
        except (ValueError, TypeError):
            value = 0
        suffix = f'（参考{value:g}倍）' if math.isfinite(value) and value > 0 else '（オッズ未取得）'
        out.append(pick + suffix)
    head = str(record.get('head', picks[0].split('-')[0]))
    evidence = record.get('group_evidence', {})
    strengths = [g for g in GROUP_NAMES if evidence.get(g, {}).get(head, 0) >= .6]
    out.append(f'1着候補：{head}号艇' + (f'／比較上位：{labels(strengths[:3])}' if strengths else ''))
    out.append('反映した材料：' + labels(g for g in GROUP_NAMES if g in evidence))
    opponents = {b for pick in picks for b in pick.split('-')[1:]}
    pairs = [p for p in record.get('inner_outer', [])
             if str(p['inner']) in opponents and str(p['outer']) in opponents]
    if pairs:
        pair = pairs[0]
        out.append(f'内外比較：{pair["inner"]}号艇が上＝{labels(pair["better"])}／'
                   f'{pair["outer"]}号艇が上＝{labels(pair["worse"])}')
    concentrated = record.get('concentrated_opponents', [])
    if concentrated:
        out.append('相手への集中：' + '、'.join(
            f'{b}号艇{record["opponent_exposure"][b]}/4点' for b in concentrated))
    try:
        fixed = datetime.datetime.fromisoformat(record['observed_at'])
        out.append(f'予想固定 {fixed:%H:%M:%S}（オッズ取得前）')
    except (ValueError, TypeError, KeyError):
        pass
    out.append('※各100円・計400円の仮想検証。確率・期待値は未算出。')
    return out
