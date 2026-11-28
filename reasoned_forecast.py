"""Odds-free, versioned rule forecast from the ChatGPT review principles.

This is an explicit, limited rule interpretation, not a replay of discretionary
ChatGPT forecasts and not a calibrated probability model. Run alongside production.
"""
import datetime
import hashlib
import itertools
import json
import math
from pathlib import Path

VERSION = 'reasoned-four-v1'
GROUPS = {
    'ability': [('b_nat_win', 1), ('r_win', 1), ('rv_win', 1)],
    'form': [('ss_rank', -1), ('ss_top3', 1)],
    'machine': [('b_mot_2r', 1), ('m_top2', 1), ('b_boat_2r', 1)],
    'foot': [('tenji', -1), ('ot_lap', -1), ('ot_turn', -1), ('ot_str', -1)],
    'start': [('r_st', -1), ('ss_st', -1), ('ex_st', -1)],
    'comment': [('cm_score', 1)],
}
COURSE = {'head': 'rc_win', 'second': '_second', 'third': '_third'}


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _rank(values, direction):
    """Six-boat percentile ranks, with exact ties receiving the same score."""
    return [(sum(direction*v > direction*w for w in values)
             + (sum(v == w for w in values)-1)/2)/5 for v in values]


def forecast(frame, observed_at):
    made = datetime.datetime.fromisoformat(observed_at)
    if made.tzinfo is None:
        raise ValueError('reason forecast timestamp needs timezone')
    raw = frame.to_dict('records') if hasattr(frame, 'to_dict') else frame
    boats = [{k: _number(v) for k, v in r.items()} for r in raw]
    if len(boats) != 6 or sorted(r.get('boat', 0) or 0 for r in boats) != list(range(1, 7)):
        raise ValueError('six distinct boat identities required')
    boats.sort(key=lambda r: r['boat'])
    if sorted(r.get('course', 0) or 0 for r in boats) != list(range(1, 7)):
        raise ValueError('verified exhibition courses required')
    for r in boats:
        win, top2, top3 = [r.get(k) for k in ('rc_win', 'rc_top2', 'rc_top3')]
        r['_second'] = top2-win if None not in (win, top2) and top2 >= win else None
        r['_third'] = top3-top2 if None not in (top2, top3) and top3 >= top2 else None
        if (r.get('ss_n') or 0) < 3:
            for k in ('ss_rank', 'ss_top3', 'ss_st'):
                r[k] = None
        # Zero machinery/local rates may mean no sample; do not call them bad feet.
        for k in ('b_mot_2r', 'm_top2', 'b_boat_2r', 'rv_win'):
            if r.get(k) == 0:
                r[k] = None
        # Exhibition F is not race F, nor a faster positive ST observation.
        if r.get('ex_f') == 1 or (r.get('ex_st') is not None and r['ex_st'] < 0):
            r['ex_st'] = None
    evidence, used, omitted = {}, {}, []

    def group(name, metrics):
        ranks = []
        used[name] = []
        for key, direction in metrics:
            values = [r.get(key) for r in boats]
            # Use the same observed metric for all six, never reward missing data.
            if any(v is None for v in values):
                omitted.append(key)
                continue
            ranks.append(_rank(values, direction))
            used[name].append(key)
        if ranks:
            evidence[name] = [sum(x)/len(x) for x in zip(*ranks)]

    for name, metrics in GROUPS.items():
        group(name, metrics)
    for role, key in COURSE.items():
        group('course_'+role, [(key, 1)])
    required = ['ability', 'foot', 'start'] + ['course_'+r for r in COURSE]
    eligible = all(k in evidence for k in required) and 'tenji' in used['foot']
    result = dict(version=VERSION,
                  rules_id=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  observed_at=observed_at, eligible=eligible,
                  used_metrics=used, omitted_metrics=sorted(set(omitted)),
                  picks=[], probability_calibrated=False)
    fields = sorted({k for metrics in GROUPS.values() for k, _ in metrics}
                    | set(COURSE.values()) | {'boat', 'course', 'ss_n', 'ex_f', 'wind', 'wave'})
    inputs = [{k: r.get(k) for k in fields} for r in boats]
    result['inputs'] = inputs
    result['inputs_id'] = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    if not eligible:
        result['reason'] = 'six-boat ability/course/start/exhibition evidence incomplete'
        return result
    shared = [k for k in GROUPS if k in evidence]
    roles = {}
    for role in COURSE:
        groups = shared + ['course_'+role]
        roles[role] = [sum(evidence[g][i] for g in groups)/len(groups) for i in range(6)]
    head = max(range(6), key=lambda i: (roles['head'][i], -i))
    pairs = []
    for b, c in itertools.permutations([i for i in range(6) if i != head], 2):
        pairs.append(dict(pick=f'{head+1}-{b+1}-{c+1}',
                          score=(roles['second'][b]+roles['third'][c])/2))
    pairs.sort(key=lambda r: (-r['score'], r['pick']))
    result.update(head=head+1, head_margin=roles['head'][head]-max(
        roles['head'][i] for i in range(6) if i != head),
        picks=[r['pick'] for r in pairs[:4]], pair_comparison=pairs,
        role_scores={role: {str(i+1): score for i, score in enumerate(scores)}
                     for role, scores in roles.items()},
        group_evidence={k: {str(i+1): v for i, v in enumerate(values)}
                        for k, values in evidence.items()})
    result['opponent_exposure'] = {str(i): sum(str(i) in p.split('-')[1:]
                                              for p in result['picks'])
                                   for i in range(1, 7) if i != head+1}
    result['concentrated_opponents'] = [b for b, n in result['opponent_exposure'].items() if n >= 3]
    # Retain a paired comparison instead of automatically dropping an inner boat
    # after a poor meet or automatically limiting an outer boat to third place.
    result['inner_outer'] = [dict(inner=i+1, outer=o+1,
        better=[g for g in shared if evidence[g][i] > evidence[g][o]],
        worse=[g for g in shared if evidence[g][i] < evidence[g][o]])
        for i in (1, 2) for o in (3, 4, 5) if i != head and o != head]
    return result


def valid_at(record, features_at, odds_started, observed, deadline, safe_min=2):
    """Only forecasts demonstrably frozen before odds retrieval enter replay."""
    try:
        times = [datetime.datetime.fromisoformat(v) if isinstance(v, str) else v
                 for v in (features_at, record['observed_at'], odds_started, observed, deadline)]
        if any(t is None or t.tzinfo is None for t in times):
            return False
        f, made, start, seen, close = times
        picks = record['picks']
        good_pick = lambda p: len(p.split('-')) == 3 and len(set(p.split('-'))) == 3 and all(x in '123456' and len(x) == 1 for x in p.split('-'))
        return (record.get('eligible') is True and record.get('version') == VERSION
                and len(record.get('rules_id', '')) == 64
                and len(picks) == len(set(picks)) == 4 and all(good_pick(p) for p in picks)
                and f <= made <= start <= seen <= close-datetime.timedelta(minutes=safe_min))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
