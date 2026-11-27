import numpy as np
import pandas as pd

import backtest


def test_uniform_probabilities_and_fixed_ticket_returns():
    class Uniform:
        def feature_name(self):
            return ['boat']
        def predict(self, frame):
            return np.ones(len(frame))
    frame = pd.DataFrame({'boat': [1, 2, 3, 4, 5, 6]})
    p = backtest.probabilities([Uniform()]*3, frame)
    np.testing.assert_allclose(p, np.full((1, 120), 1/120))
    np.testing.assert_allclose(backtest.temper(p, 1.6).sum(1), [1])
    p = np.full((2, 120), .001)
    p[:, 0], p[:, 1] = .7, .1
    p /= p.sum(1, keepdims=True)
    stat, _, _, _ = backtest.metrics(p, np.array([0, 1]), np.array([500, 900]))
    assert stat['top1_hit_pct'] == 50
    assert stat['top2_hit_pct'] == 100
    assert stat['fixed1_roi_pct'] == 250
    assert stat['fixed2_roi_pct'] == 350


def test_tied_results_excluded_before_scoring():
    d = pd.DataFrame(dict(date=[20261126]*12, venue=[1]*12,
                          race=[1]*6+[2]*6, boat=list(range(1, 7))*2,
                          rank=[1, 2, 3, 4, 5, 6, 1, 2, 2, 4, 5, 6]))
    result = backtest.complete(d)
    assert len(result) == 6 and result.race.eq(1).all()
    assert backtest.outcomes(result).tolist() == [0]
