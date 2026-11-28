import datetime
import json

import pandas as pd

import retrain_once


def write_inputs(folder, **changes):
    hd = '20261128'
    m = dict(jcd=18, rno=4, asof_verified=True, complete_market=True,
             observed_at='2026-11-28T10:00:00+09:00', deadline='2026-11-28T10:09:00+09:00',
             features_observed_at='2026-11-28T09:59:00+09:00')
    m.update(changes.get('market', {}))
    f = dict(jcd=18, rno=4, observed_at=m['features_observed_at'],
             boats=[dict(boat=i, course=i, tenji=6.8) for i in range(1, 7)])
    r = dict(jcd=18, rno=4, tri='1-2-5', tri_pay='480', tri_payouts='{"1-2-5":480}',
             refund_boats='', race_status='settled', got_at='10:20')
    r.update(changes.get('result', {}))
    (folder/f'market_{hd}.jsonl').write_text(json.dumps(m)+'\n')
    (folder/f'features_{hd}.jsonl').write_text(json.dumps(f)+'\n')
    pd.DataFrame([r]).to_csv(folder/f'results_{hd}.csv', index=False)


def test_fresh_pre_race_features_get_labels_only_after_settlement(tmp_path):
    write_inputs(tmp_path)
    asof = datetime.datetime.fromisoformat('2026-11-28T10:21:00+09:00')
    frame, races = retrain_once.current_rows(tmp_path, '20261128', asof)
    assert races == 1 and len(frame) == 6
    assert frame.loc[frame.win == 1, 'boat'].tolist() == [1]
    assert frame.loc[frame.top2 == 1, 'boat'].tolist() == [1, 2]
    assert frame.loc[frame.top3 == 1, 'boat'].tolist() == [1, 2, 5]
    assert 'wind' not in frame and frame.tenji.eq(6.8).all()
    assert retrain_once.current_rows(tmp_path, '20261128', asof.replace(minute=10))[1] == 0


def test_excludes_late_features_refunds_and_ambiguous_results(tmp_path):
    asof = datetime.datetime.fromisoformat('2026-11-28T10:21:00+09:00')
    cases = [dict(market=dict(observed_at='2026-11-28T10:08:00+09:00')),
             dict(result=dict(refund_boats='6')),
             dict(result=dict(tri_payouts='{"1-2-5":480,"1-5-2":300}')),
             dict(market=dict(asof_verified=False))]
    for change in cases:
        write_inputs(tmp_path, **change)
        assert retrain_once.current_rows(tmp_path, '20261128', asof)[1] == 0
