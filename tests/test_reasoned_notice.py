import copy
import datetime
from itertools import permutations

import pytest

import predict
import reasoned_notice


def saved():
    return dict(eligible=True, version='reasoned-four-v1', rules_id='a'*64,
                observed_at='2026-11-28T10:00:00+09:00', head=1,
                picks=['1-2-5', '1-5-2', '1-2-4', '1-5-4'],
                group_evidence={'ability': {'1': .8}, 'foot': {'1': .7},
                                'start': {'1': .4}},
                inner_outer=[dict(inner=2, outer=5, better=['foot'], worse=['ability'])],
                concentrated_opponents=['2', '5'], opponent_exposure={'2': 3, '5': 3})


def test_notice_shows_recorded_picks_evidence_and_separate_virtual_scope():
    record = saved()
    original = copy.deepcopy(record)
    body = '\n'.join(reasoned_notice.lines(record, {'1-2-5': 12.3}))
    assert '1-2-5（参考12.3倍）' in body
    assert body.index('1-2-5') < body.index('1-5-2') < body.index('1-2-4')
    assert '1着候補：1号艇' in body and '能力・展示の足' in body
    assert '2号艇が上＝展示の足／5号艇が上＝能力' in body
    assert '2号艇3/4点' in body and '5号艇3/4点' in body
    assert '予想固定 10:00:00（オッズ取得前）' in body
    assert '計400円の仮想検証' in body and '確率・期待値は未算出' in body
    assert record == original


def test_absent_or_invalid_record_is_a_hold_without_invented_picks():
    for record in (None, {}, dict(saved(), eligible=False),
                   dict(saved(), picks=None), dict(saved(), picks=['1-2-5']*4)):
        body = '\n'.join(reasoned_notice.lines(record))
        assert '保留' in body and '1-2-5' not in body


def test_missing_or_non_finite_odds_are_not_fabricated():
    body = '\n'.join(reasoned_notice.lines(saved(), {'1-2-5': float('nan'), '1-5-2': 'invalid'}))
    assert body.count('オッズ未取得') == 4
    assert 'nan' not in body and '%' not in body


@pytest.mark.parametrize('buy', [True, False])
@pytest.mark.parametrize('candidate', [True, False])
def test_buy_and_skip_use_one_existing_delivery_with_verified_record(monkeypatch, tmp_path, buy, candidate):
    now = datetime.datetime.fromisoformat('2026-11-28T10:00:01+09:00')
    close = now+datetime.timedelta(minutes=5)
    record = saved()
    probability = {'-'.join(map(str, p)): 1/120 for p in permutations(range(1, 7), 3)}
    probability = dict.fromkeys(probability, .8/119)
    probability['1-2-5'] = .2
    odds = dict.fromkeys(probability, 10)
    sent, notices, logged = [], [], []

    def prediction(*args, **kwargs):
        predict.LAST.clear()
        predict.LAST['reasoned'] = record
        predict.LAST['features_observed_at'] = record['observed_at']
        return object()

    def send(title, body, **kwargs):
        sent.append((title, body, kwargs))
        return dict(status=predict.SENT_OK, http=200, ntfy_id='test', error='')

    monkeypatch.setattr(predict, 'LOGS', str(tmp_path))
    monkeypatch.setattr(predict, 'now_jst', lambda: now)
    monkeypatch.setattr(predict, 'predict', prediction)
    monkeypatch.setattr(predict, 'tri_probs', lambda _: probability)
    monkeypatch.setattr(predict, 'odds_map', lambda *args: odds)
    monkeypatch.setattr(predict, 'combos', lambda *args: [(1.2 if buy else .8, '1-2-5', .2 if buy else .08, 6)])
    monkeypatch.setattr(predict, 'save_sample', lambda *args: None)
    monkeypatch.setattr(predict, 'entry_text', lambda: '進入1-2-3-4-5-6')
    monkeypatch.setattr(predict, 'rank_text', lambda *args: '順位の表示')
    monkeypatch.setattr(predict, 'send_notify', send)
    monkeypatch.setattr(predict, 'write_notify', lambda hd, row: notices.append(row))
    monkeypatch.setattr(predict, 'write_log', lambda hd, rows: logged.extend(rows))
    predict.run_race([], {'_model_ids': ['baseline']}, 18, 5, '20261128', close, now, cand=candidate)
    if buy:
        assert len(sent) == 1
        assert sent[0][0].startswith('統合予想 ')
        assert 'AI＋判断ルールの共通予想' in sent[0][1]
        assert '検証4点' not in sent[0][1] and '1-5-2' not in sent[0][1]
        assert sent[0][2]['notification_id'] == 'consensus-v1-20261128'
        assert [r['pick'] for r in sent[0][2]['receipt']['bets']] == ['1-2-5']
        assert [r['pick'] for r in logged] == ['1-2-5']
        assert notices[0]['policy'] == 'consensus-v1'
    else:
        assert len(sent) == int(candidate)
        if candidate:
            assert sent[0][0].startswith('見送り ')
            assert '検証4点' not in sent[0][1] and '1-2-5' not in sent[0][1]
            assert sent[0][2]['notification_id'] == '20261128-18-05'
        assert notices[0]['status'] == predict.ST_SKIP
        assert logged[0]['pick'] == ''


def test_skip_notice_never_sends_after_the_existing_deadline_guard(monkeypatch):
    sent = []
    monkeypatch.setattr(predict, 'remain', lambda _: 1)
    monkeypatch.setattr(predict, 'send_notify', lambda *args, **kwargs: sent.append(args))
    predict.send_skip_notice('徳山', 4, None, 'test', '条件未達', [], saved())
    assert not sent
