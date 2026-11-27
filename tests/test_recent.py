import numpy as np
import pandas as pd
import recent


def test_recent_window_excludes_prediction_day_and_old_results():
    history = pd.DataFrame(dict(racer=[1]*8, course=[1]*8, venue=[1]*8,
                                date=[20200101]+[20200401]*5+[20200402]*2,
                                win=[1]+[0]*5+[1]*2, top2=[1]*8,
                                top3=[1]*8, st_ok=[.1]*8))
    q = pd.DataFrame([dict(racer=1, course=1, venue=1, date=20200402, r_win=.5, rc_win=.5)])
    result = recent.attach(history, q)
    assert result.r90_n.iloc[0] == 5 and result.r90_win.iloc[0] == 0
    assert result.rc180_n.iloc[0] == 6
    assert np.isclose(result.rc180_win.iloc[0], 1/6)
    assert np.isclose(result.r90_st.iloc[0], .1)


def test_recent_course_queries_past_actual_course_and_preserves_order():
    h = pd.DataFrame(dict(racer=[1]*10, course=[1]*5+[2]*5, venue=[1]*10,
                          date=[20200401]*10, win=[0]*5+[1]*5,
                          top2=[1]*10, top3=[1]*10, st_ok=[.1]*10))
    q = pd.DataFrame([dict(racer=1, course=c, venue=1, date=20200402, r_win=.5, rc_win=.5) for c in [2, 1]])
    result = recent.attach(h, q)
    assert result.rc180_win.tolist() == [1, 0]
    q['date'] = 20210402
    assert recent.attach(h, q).r90_n.eq(0).all()
    assert recent.attach(h, q).r90_win.isna().all()
