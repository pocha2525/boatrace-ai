import csv
import json
from pathlib import Path

import pytest

import consensus
import morning
import predict


def record():
    return dict(eligible=True, picks=['1-2-3', '1-3-2', '1-2-4', '1-4-2'])


def test_only_shared_top_four_with_individual_probability_and_ev():
    rows = [(2., '1-5-6', .22, 10), (1.5, '1-3-2', .15, 10),
            (.99, '1-2-3', .14, 7), (1.2, '1-2-4', .11, 11),
            (1.3, '1-4-2', .13, 10)]
    # Sorted top four includes 142, displaces 124. No new probabilities.
    assert [x[1] for x in consensus.select(rows, record())] == ['1-3-2', '1-4-2']
    assert consensus.select(rows, None) == []
    assert consensus.select(rows, dict(record(), eligible=False)) == []
    assert consensus.select(rows, dict(record(), picks=['1-2-3']*4)) == []


def test_top_four_limit_and_max_two():
    rows = [(2, '1-5-6', .3, 10), (2, '1-6-5', .25, 10),
            (2, '2-5-6', .2, 10), (2, '2-6-5', .15, 10), (2, '1-2-3', .14, 10)]
    assert consensus.select(rows, record()) == []
    rows = [(2, p, .2-i*.01, 10) for i, p in enumerate(record()['picks'])]
    assert len(consensus.select(rows, record())) == 2


@pytest.mark.parametrize('status,used', [(predict.SENT_OK, True), (predict.SENT_UNKNOWN, True),
                                       ('送信予約', True), (predict.SENT_FAIL, False), (predict.ST_SKIP, False)])
def test_daily_cap_from_csv_and_legacy_exclusion(tmp_path, status, used):
    path = tmp_path/'notify_20261128.csv'
    def write(policy):
        with path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['policy', 'status'])
            w.writeheader(); w.writerow(dict(policy=policy, status=status))
    write('')
    assert not consensus.day_used(tmp_path, '20261128')
    write(consensus.VERSION)
    assert consensus.day_used(tmp_path, '20261128') is used


@pytest.mark.parametrize('status,used', [('送信予約', True), (predict.SENT_UNKNOWN, True),
                                       (predict.SENT_OK, True), (predict.SENT_FAIL, False)])
def test_daily_cap_survives_lost_final_csv(tmp_path, status, used):
    folder = tmp_path/'delivery'; folder.mkdir()
    (folder/'claim.json').write_text(json.dumps(dict(notification_id=consensus.daily_id('20261128'), status=status)))
    assert consensus.day_used(tmp_path, '20261128') is used
    assert not consensus.day_used(tmp_path, '20261129')


def test_morning_saves_internal_candidates_without_any_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(predict, 'LOGS', str(tmp_path))
    monkeypatch.setattr(predict, 'load_model', lambda: ([], {}))
    monkeypatch.setattr(predict, 'today_venues', lambda _: [])
    monkeypatch.setattr(morning, 'LOGS', str(tmp_path))
    monkeypatch.setattr(morning, 'send', lambda *a: pytest.fail('candidate notification disabled'))
    morning.main()
    assert len(list(tmp_path.glob('morning_*.csv'))) == 1
