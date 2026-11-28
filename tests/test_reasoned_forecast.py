import copy
import datetime
import json
from itertools import permutations

import pandas as pd

import policy_replay
import reasoned_forecast as rules


def frame():
    return [dict(boat=b, course=b, b_nat_win=8-b*.5, r_win=.65/b,
                 rv_win=6-b*.2, rc_win=.8 if b == 1 else .05,
                 rc_top2=.9 if b == 1 else .55-b*.03,
                 rc_top3=.95 if b == 1 else .8-b*.03,
                 ss_n=5, ss_rank=1+b*.3, ss_top3=1-b*.05,
                 b_mot_2r=45-b, m_top2=.45-b*.01, b_boat_2r=40-b,
                 tenji=6.5+b*.01, ot_turn=5.4+b*.03, ot_lap=37+b*.1,
                 ot_str=7.8+b*.02, r_st=.12+b*.01, ss_st=.12+b*.01,
                 ex_st=.10+b*.01, ex_f=0, wind=2, wave=2)
            for b in range(1, 7)]


NOW = '2026-11-28T12:00:00+09:00'


def test_roles_and_pair_audit_are_separate_without_fake_probability():
    forecast = rules.forecast(frame(), NOW)
    assert forecast['eligible'] and len(set(forecast['picks'])) == 4
    assert forecast['head'] == 1
    assert set(forecast['role_scores']) == {'head', 'second', 'third'}
    assert len(forecast['pair_comparison']) == 20
    assert len(forecast['inner_outer']) == 6
    assert not forecast['probability_calibrated']
    assert 'probabilities' not in forecast


def test_odds_result_and_input_row_order_cannot_change_forecast():
    original = rules.forecast(frame(), NOW)
    changed = frame()[::-1]
    for b in changed:
        b.update(odds=100000, finish=1, tri_pay=1000000)
    other = rules.forecast(changed, NOW)
    assert original['picks'] == other['picks']
    assert original['inputs_id'] == other['inputs_id']
    changed = frame(); changed[-1]['ex_st'] = .001
    assert rules.forecast(changed, NOW)['head'] == 1


def test_outer_boat_can_be_second_and_ranking_is_not_a_lane_rule():
    data = frame()
    for b in data:
        for k in ('b_nat_win', 'r_win', 'rv_win', 'ss_rank', 'ss_top3',
                  'b_mot_2r', 'm_top2', 'b_boat_2r', 'tenji', 'ot_turn',
                  'ot_lap', 'ot_str', 'r_st', 'ss_st', 'ex_st'):
            b[k] = data[0][k]
    data[3].update(rc_win=.05, rc_top2=.95, rc_top3=.96)
    f = rules.forecast(data, NOW)
    assert any(p.split('-')[1] == '4' for p in f['picks'])
    data[5].update(rc_win=.99, rc_top2=.995, rc_top3=.999)
    assert rules.forecast(data, NOW)['head'] == 6


def test_unknown_foot_or_course_prevents_a_forecast_and_f_is_not_fast_st():
    data = frame(); data[0]['tenji'] = None
    assert not rules.forecast(data, NOW)['eligible']
    data = frame(); data[-1]['course'] = 1
    try:
        rules.forecast(data, NOW)
    except ValueError:
        pass
    else:
        raise AssertionError('duplicate exhibition courses accepted')
    data = frame(); data[1].update(ex_f=1, ex_st=.001)
    f = rules.forecast(data, NOW)
    assert 'ex_st' in f['omitted_metrics']
    assert 'ex_st' not in f['used_metrics']['start']
    data = frame(); data[-1]['ss_n'] = 0
    assert 'form' not in rules.forecast(data, NOW)['group_evidence']


def capture(tmp_path, forecast):
    now = datetime.datetime.fromisoformat(NOW)
    start = now+datetime.timedelta(seconds=1)
    seen = now+datetime.timedelta(seconds=2)
    close = now+datetime.timedelta(minutes=5)
    p = {'-'.join(map(str, x)): 1/120 for x in permutations(range(1, 7), 3)}
    return policy_replay.capture(tmp_path, '20261128', 18, 12, close, start,
        seen, p, dict.fromkeys(p, 10), ['baseline'], {}, NOW, reasoned=forecast)


def test_only_pre_odds_forecasts_are_recorded_and_settled(tmp_path):
    f = rules.forecast(frame(), NOW)
    row = capture(tmp_path, f)
    assert row['reasoned']['picks'] == f['picks']
    table = pd.DataFrame([dict(jcd=18, rno=12, tri=f['picks'][0], tri_pay=800,
                               race_status='settled', refund_boats='')])
    policy_replay.report(tmp_path, lambda _: table, '20261128')
    detail = pd.read_csv(tmp_path/'policy_replay.csv')
    rule = detail[detail.policy == 'chatgpt_rules_v1_four'].iloc[0]
    assert rule.investment == 400 and rule.returned == 800 and rule.hits == 1
    assert pd.isna(rule.log_probability)
    late = copy.deepcopy(f); late['observed_at'] = '2026-11-28T12:00:03+09:00'
    assert 'reasoned' not in capture(tmp_path, late)


def test_first_snapshot_is_fixed_and_no_retrospective_predictions_added(tmp_path):
    row = capture(tmp_path, rules.forecast(frame(), NOW))
    first = copy.deepcopy(row); first.pop('reasoned')
    first['observed_at'] = '2026-11-28T12:00:01+09:00'
    (tmp_path/'market_20261128.jsonl').write_text(json.dumps(first)+'\n'+json.dumps(row)+'\n')
    policy_replay.report(tmp_path, lambda _: None, '20261128')
    detail = pd.read_csv(tmp_path/'policy_replay.csv')
    assert 'chatgpt_rules_v1_four' not in set(detail.policy)
