import datetime
from itertools import permutations
import pandas as pd
import policy_replay


def test_replay_distinguishes_the_second_ticket_rule():
    p = {'1-2-3': .20, '1-3-2': .15}
    odds = {'1-2-3': 6, '1-3-2': 6}
    assert policy_replay.select(p, odds) == ['1-2-3', '1-3-2']
    assert policy_replay.select(p, odds, ev2=1) == ['1-2-3']
    assert policy_replay.select(p, odds, max_bets=1) == ['1-2-3']
    assert policy_replay.select(p, {'1-2-3': 4, '1-3-2': 100}) == []


def test_snapshot_requires_a_complete_market_and_time_margin(tmp_path):
    now = datetime.datetime(2026, 11, 27, 12, tzinfo=datetime.timezone.utc)
    p = {'-'.join(map(str, x)): 1/120 for x in permutations(range(1, 7), 3)}
    odds = {k: 120 for k in p}
    row = policy_replay.capture(str(tmp_path), '20261127', 1, 1, now+datetime.timedelta(minutes=3), now, now, p, odds, ['abc'], {})
    assert row['asof_verified']
    row = policy_replay.capture(str(tmp_path), '20261127', 1, 2, now+datetime.timedelta(minutes=1), now, now, p, odds, ['abc'], {})
    assert not row['asof_verified']
    row = policy_replay.capture(str(tmp_path), '20261127', 1, 3, now+datetime.timedelta(minutes=3), now, now, p, {}, ['abc'], {})
    assert not row['asof_verified']


def test_virtual_policy_settlement_handles_refunds_and_pending(tmp_path):
    import json
    record = dict(hd='20261127', jcd=1, rno=1, observed_at='2026-11-27T12:00:00+09:00',
                  asof_verified=True, probabilities={'1-2-3': .2, '1-3-2': .15},
                  odds={'1-2-3': 6, '1-3-2': 6}, policy={})
    (tmp_path/'market_20261127.jsonl').write_text(json.dumps(record)+'\n'+json.dumps(record)+'\n')
    def results(_):
        return pd.DataFrame([dict(jcd=1, rno=1, tri='1-3-2', tri_pay=900, refund_boats='')])
    summary = policy_replay.report(str(tmp_path), results, '20261127').set_index('policy')
    assert summary.loc['current_two', 'investment'] == 200
    assert summary.loc['current_two', 'returned'] == 900
    assert summary.loc['first_only', 'returned'] == 0
    summary = policy_replay.report(str(tmp_path), lambda _: None, '20261127').set_index('policy')
    assert summary.loc['current_two', 'pending'] == 2
    assert summary.loc['current_two', 'investment'] == 0
