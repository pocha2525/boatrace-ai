import numpy as np
import pandas as pd
import threshold_audit as audit
from backtest import saved_predictions


def frame():
    return pd.DataFrame(dict(date=[20261101,20261102,20261103], venue=[1]*3, race=[1]*3,
        top1_probability=[.12,.15,.119], tri_pay=[500,900,99999], tri=['1-2-3','1-4-2','1-2-3'],
        pick1=['1-2-3']*3,pick2=['1-3-2']*3,pick3=['1-4-2']*3,pick4=['1-2-4']*3))


def test_four_ticket_cost_and_third_pick_win():
    one=audit.score(frame(),.12,1,0)
    two=audit.score(frame(),.12,2,0)
    four=audit.score(frame(),.12,4,0)
    assert one['races']==2 and one['roi_pct']==250
    assert two['roi_pct']==125 and two['investment']==400
    assert four['roi_pct']==175 and four['investment']==800 and four['hits']==2
    assert four['roi_without_best_race_pct']==125
    assert four['max_drawdown']==0
    assert not audit.score(frame(),.3,4)['eligible_for_selection']
    assert audit.score(frame(),.3,4)['roi_pct'] is None


def test_selection_rejects_tiny_high_roi_and_uses_only_validation():
    rows=[dict(eligible_for_selection=False,low95_roi_pct=500,races=2,threshold_pct=20),
          dict(eligible_for_selection=True,low95_roi_pct=90,races=60,threshold_pct=12),
          dict(eligible_for_selection=True,low95_roi_pct=95,races=80,threshold_pct=10)]
    assert audit.choose(rows)['threshold_pct']==10
    assert audit.choose(rows[:1]) is None


def test_saved_four_picks_are_distinct_and_probability_ranked():
    p=np.full((1,120),.001)
    p[0,:4]=[.1,.4,.3,.2]
    p/=p.sum(axis=1,keepdims=True)
    result=saved_predictions(pd.DataFrame({'date':[20261101]}),p,'candidate')
    assert result[['pick1','pick2','pick3','pick4']].nunique(axis=1).iloc[0]==4
    assert result.top1_probability.iloc[0]>result.top4_probability.iloc[0]
