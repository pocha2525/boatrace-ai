import csv
import json
import numpy as np
import ticket_comparison
import backtest
from notify_evaluation import message


def test_two_ticket_cost_is_doubled_and_low_probability_is_excluded():
    rows = [dict(top1_probability=.2, tri_pay=500, pick1='1-2-3', pick2='1-3-2', tri='1-2-3'),
            dict(top1_probability=.12, tri_pay=900, pick1='1-2-3', pick2='1-3-2', tri='1-3-2'),
            dict(top1_probability=.119, tri_pay=99999, pick1='1-2-3', pick2='1-3-2', tri='1-2-3')]
    result = ticket_comparison.metrics(rows)
    assert result['selected12_n'] == 2
    assert result['selected12_hit_pct'] == 50
    assert result['selected12_two_hit_pct'] == 100
    assert result['one_investment'] == 200 and result['one_profit'] == 300
    assert result['two_investment'] == 400 and result['two_profit'] == 1000
    assert result['selected12_two_roi_pct'] == 350
    p = np.full((3, 120), .001)
    p[:, 0], p[:, 1] = .2, .15
    p[2, :] = 1/120
    p /= p.sum(1, keepdims=True)
    stat, *_ = backtest.metrics(p, np.array([0, 1, 0]), np.array([500, 900, 99999]))
    assert stat['selected12_two_roi_pct'] == result['selected12_two_roi_pct']
    assert stat['selected12_two_hit_pct'] == result['selected12_two_hit_pct']


def test_enrich_checks_saved_predictions_and_notification_includes_two_points(tmp_path):
    summary = dict(holdout_from=20261113, holdout_until=20261126, metrics={
        'candidate': dict(features=67, selected12_n=1, selected12_hit_pct=0, selected12_roi_pct=0)})
    path = tmp_path/'summary.json'
    row = dict(arm='candidate', top1_probability=.2, tri_pay=900, pick1='1-2-3', pick2='1-3-2', tri='1-3-2')
    with (tmp_path/'races.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
    result = ticket_comparison.enrich(summary, path)
    assert result['metrics']['candidate']['two_profit'] == 700
    assert '固定2点: 的中100.0%／回収450.0%' in message([result], 'run', 'owner/repo')
    result['metrics']['candidate']['selected12_n'] = 2
    import pytest
    with pytest.raises(ValueError, match='previous score'):
        ticket_comparison.enrich(result, path)
