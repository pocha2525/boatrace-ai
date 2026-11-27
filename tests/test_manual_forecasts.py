import json
import pandas as pd
import pytest
import manual_forecasts


def test_manual_actual_and_equal_stakes_are_not_mixed(tmp_path):
    row=dict(hd='20261127',jcd=11,rno=5,published_at='2026-11-27T12:05:38+09:00',deadline='2026-11-27T12:09:00+09:00',source='predeadline_record',stakes={'1-4-5':5600,'1-5-4':2500,'1-4-6':1400,'1-5-6':500})
    p=tmp_path/'manual_forecasts.jsonl';p.write_text(json.dumps(row)+'\n')
    results=lambda _:pd.DataFrame([dict(jcd=11,rno=5,tri='1-4-5',tri_pay=600,refund_boats='')])
    s=manual_forecasts.report(tmp_path,results,'20261127').set_index('policy')
    assert s.loc['manual_actual_allocation','returned']==33600
    assert s.loc['manual_equal100','returned']==600
    s=manual_forecasts.report(tmp_path,lambda _:None,'20261127')
    assert s.investment.eq(0).all() and s.pending.eq(1).all()
    row['published_at']='2026-11-27T12:10:00+09:00';p.write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError,match='締切前'):
        manual_forecasts.report(tmp_path,results,'20261127')
