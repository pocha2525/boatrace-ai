"""Regression checks for the inherited morning/data/report paths. No live requests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import numpy as np
import pandas as pd

import backfill
import before
import bfile
import common
import morning
import predict
import report

ROOT = Path(__file__).resolve().parents[1]
CARD = (ROOT / 'logs/samples/20261127_racelist.html').read_text()


def test_parallel_race_state_is_isolated():
    state = common.ThreadState()
    barrier = threading.Barrier(4)
    def one(i):
        state['race'] = i
        barrier.wait()
        return state['race']
    with ThreadPoolExecutor(4) as pool:
        assert list(pool.map(one, range(4))) == list(range(4))
    assert not state


def test_morning_passes_card_features_without_fetching_exhibition(monkeypatch, tmp_path):
    monkeypatch.setattr(morning, 'page', lambda *a: CARD)
    monkeypatch.setattr(predict, 'race_info', morning.info_morning)
    monkeypatch.setattr(predict, 'LOGS', str(tmp_path))
    def forbidden(*a, **k):
        raise AssertionError('morning must not fetch live exhibition/comments')
    # Raising here is caught by predict(), so also count calls explicitly.
    calls = []
    def track(*a, **k):
        calls.append(1)
        return forbidden()
    monkeypatch.setattr(predict.ot, 'fetch', track)
    monkeypatch.setattr(predict.cm, 'fetch', track)
    class Model:
        def feature_name(self):
            return ['b_cls', 'b_nat_win', 'ex_st', 'tenji']
        def predict(self, frame):
            assert len(frame) == 6
            assert frame['b_cls'].notna().all()
            assert frame['b_nat_win'].notna().all()
            assert frame[['ex_st', 'tenji']].isna().all().all()
            return np.ones(6) * .2
    lk = {name: {} for _, _, name in common.FEATS}
    result = predict.predict([Model()] * 3, lk, 24, 8, '20261127', strict=False)
    assert result is not None
    assert calls == []
    assert len(predict.tri_probs(result)) == 120
    assert np.isclose(sum(predict.tri_probs(result).values()), 1)


def test_saved_models_can_predict_morning(monkeypatch, tmp_path):
    monkeypatch.setattr(morning, 'page', lambda *a: CARD)
    monkeypatch.setattr(predict, 'race_info', morning.info_morning)
    monkeypatch.setattr(predict, 'LOGS', str(tmp_path))
    models, tables = predict.load_model()
    assert all(set(common.X) <= set(model.feature_name()) for model in models)
    assert len({tuple(model.feature_name()) for model in models}) == 1
    result = predict.predict(models, tables, 24, 8, '20261127', strict=False)
    probabilities = predict.tri_probs(result)
    assert np.isclose(sum(probabilities.values()), 1)
    assert all(np.isfinite(x) and 0 <= x <= 1 for x in probabilities.values())


def test_missing_exhibition_remains_missing():
    d = before.add_rel(pd.DataFrame({'boat': range(1, 7)}))
    assert d[['ex_st', 'ex_st_rank', 'ex_st_d']].isna().all().all()


def test_backfill_retries_failed_pages(monkeypatch, tmp_path):
    day = common.now_jst().date() - common.datetime.timedelta(days=1)
    hd = f'{day:%Y%m%d}'
    monkeypatch.setattr(backfill, 'ROOT', str(tmp_path))
    monkeypatch.setattr(backfill, 'DONE', str(tmp_path / 'done.txt'))
    monkeypatch.setattr(backfill.train, 'START', day)
    k = tmp_path / 'K.txt'; k.write_text('test')
    monkeypatch.setattr(backfill, 'k_path', lambda d: str(k))
    monkeypatch.setattr(backfill, 'read_k', lambda p: ([dict(venue=11, race=5, boat=b, course=b) for b in range(1, 7)], [{'venue': 11, 'race': 5}]))
    monkeypatch.setattr(backfill, 'one', lambda args: [])
    used, done = backfill.main(budget=1)
    assert used == 1 and done == []
    assert not (tmp_path / 'done.txt').exists()
    # Repair a formerly incorrect completion marker too.
    (tmp_path / 'done.txt').write_text(hd + '\n')
    assert backfill.done_days() == set()


def test_backfill_requires_all_six_boats_and_actual_exhibition(tmp_path):
    p = tmp_path / 'before.csv'
    d = pd.DataFrame({'jcd': [11]*6, 'rno': [5]*6,
                      'boat': range(1, 7), 'ex_st': [.1]*6, 'tenji': [6.8]*6})
    d.iloc[:5].to_csv(p, index=False)
    assert not backfill.complete_races(p)
    d.to_csv(p, index=False)
    assert backfill.complete_races(p) == {(11, 5)}
    d.loc[2, 'ex_st'] = np.nan
    d.to_csv(p, index=False)
    assert not backfill.complete_races(p)


def test_header_merge_does_not_break_notification_log(monkeypatch, tmp_path):
    p = tmp_path / 'notify.csv'
    p.write_text('notify_id,hd,jcd,rno,status\nnotify_id,hd,jcd,rno,status\nx,20261127,11,5,条件未達\n')
    assert len(predict.read_log(p)) == 1


def test_unresolved_result_is_not_cancellation(monkeypatch, tmp_path):
    monkeypatch.setattr(report, 'LOGS', str(tmp_path))
    pd.DataFrame([dict(notify_id='x', hd='20261127', jcd='11', rno='5',
                       venue='びわこ', deadline='12:09', morning='', picks='1-4-5',
                       odds='5.2', evs='1.1', reason='', status='受付成功')]).to_csv(
        tmp_path / 'notify_20261127.csv', index=False)
    monkeypatch.setattr(report, 'results_for', lambda h: pd.DataFrame(
        [dict(jcd=11, rno=5, tri=None, tri_pay=None)]))
    j = report.judged_day('20261127')
    assert j.iloc[0]['結果'] == report.NO_TRI
    assert j.iloc[0]['当たり'] == '-'
    assert j.iloc[0]['払戻'] == ''


def test_pending_days_are_reconciled_again(monkeypatch, tmp_path):
    p = tmp_path / 'judged_all.csv'
    monkeypatch.setattr(report, 'JUDGED', str(p))
    monkeypatch.setattr(report, 'LOGS', str(tmp_path))
    old = {c: '' for c in report.JUDGED_COLS}
    old.update(日付='2026/11/27', 結果='中止・返還')
    pd.DataFrame([old]).to_csv(p, index=False)
    (tmp_path / 'notify_20261127.csv').write_text('placeholder')
    new = dict(old, 結果='1-4-5')
    monkeypatch.setattr(report, 'judged_day', lambda h: pd.DataFrame([new]))
    report.update_judged('20261127')
    out = pd.read_csv(p)
    assert len(out) == 1 and out.iloc[0]['結果'] == '1-4-5'


def test_failed_reconciliation_keeps_existing_pending_rows(monkeypatch, tmp_path):
    p = tmp_path / 'judged_all.csv'
    monkeypatch.setattr(report, 'JUDGED', str(p))
    monkeypatch.setattr(report, 'LOGS', str(tmp_path))
    old = {c: '' for c in report.JUDGED_COLS}
    old.update(日付='2026/11/26', 結果='未確定')
    pd.DataFrame([old]).to_csv(p, index=False)
    for hd in ['20261126', '20261127']:
        (tmp_path / f'notify_{hd}.csv').write_text('placeholder')
    new = dict(old, 日付='2026/11/27', 結果='1-4-5')
    monkeypatch.setattr(report, 'judged_day', lambda h: None if h == '20261126' else pd.DataFrame([new]))
    report.update_judged('20261127')
    out = pd.read_csv(p)
    assert len(out) == 2 and out.iloc[0]['結果'] == '未確定'
