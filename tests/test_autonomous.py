import datetime as dt
import json
import sys
import types

import autonomous


def stub_analysis(monkeypatch):
    monkeypatch.setitem(sys.modules, 'fast_analysis', types.SimpleNamespace(run=lambda *a: dict(metadata=dict(status='cached'))))


def test_clock_plan_and_report_date_are_jst():
    morning = dt.datetime(2026, 11, 28, 8, tzinfo=autonomous.JST)
    assert autonomous.phases('auto', morning) == ['morning','predict']
    assert autonomous.phases('auto', morning.replace(hour=3)) == []
    assert autonomous.phases('auto', morning.replace(hour=23)) == ['results','report']
    assert autonomous.target_day('report', morning) == '20261127'
    assert autonomous.target_day('report', morning.replace(hour=23)) == '20261128'


def test_shared_heartbeat_skips_duplicate_daily_and_recent_predict(monkeypatch, tmp_path):
    stub_analysis(monkeypatch)
    now = dt.datetime.now(autonomous.JST)
    called=[]
    def work(phase, hd): called.append((phase,hd)); return dict(ok=True)
    autonomous.operate(tmp_path,'predict',now,work)
    autonomous.operate(tmp_path,'predict',now+dt.timedelta(minutes=3),work)
    assert len(called) == 1
    autonomous.operate(tmp_path,'predict',now+dt.timedelta(minutes=5),work)
    assert len(called) == 2
    autonomous.operate(tmp_path,'morning',now,work)
    autonomous.operate(tmp_path,'morning',now+dt.timedelta(hours=1),work)
    assert len([x for x in called if x[0]=='morning']) == 1


def test_failed_phase_is_saved_and_retried_without_touching_models(monkeypatch,tmp_path):
    stub_analysis(monkeypatch)
    now=dt.datetime.now(autonomous.JST)
    calls=[]
    def fail(phase, hd): calls.append(phase); raise TimeoutError('not persisted secret detail')
    result=autonomous.operate(tmp_path,'predict',now,fail)
    assert result['errors']==[dict(phase='predict',error_type='TimeoutError')]
    saved=json.loads((tmp_path/'logs/auto_runtime.json').read_text())
    assert saved['tasks'][f'predict-{now:%Y%m%d}']['consecutive_failures']==1
    assert 'secret detail' not in json.dumps(saved)
    assert autonomous.due('predict',now,saved)
    assert not (tmp_path/'model').exists()


def test_read_only_check_never_executes_or_sends(tmp_path):
    model=tmp_path/'model';model.mkdir()
    for name in ('win','top2','top3'):(model/f'model_{name}.txt').write_text(name)
    result=autonomous.check(tmp_path,dt.datetime.now(autonomous.JST))
    assert len(result['model_hashes'])==3
    assert result['notification_sent'] is False and not (tmp_path/'logs').exists()


def test_report_prefers_saved_official_results_and_handles_known_legacy_header(monkeypatch,tmp_path):
    import pandas as pd
    import report
    monkeypatch.setattr(report,'LOGS',str(tmp_path))
    pd.DataFrame([dict(jcd=18,rno=4,tri='1-2-5',tri_pay=480,race_status='settled')]).to_csv(tmp_path/'results_20261128.csv',index=False)
    monkeypatch.setattr(report,'download_k',lambda *a: (_ for _ in ()).throw(AssertionError('must reuse official result')))
    assert report._results_for('20261128').iloc[0].tri=='1-2-5'
    monkeypatch.setattr(report,'MODEL',str(tmp_path))
    (tmp_path/'history.csv').write_text('202611232125,v2,known legacy row\n')
    assert report.model_history()[-1]==(202611232125,'v2')


def test_slow_cycle_does_not_skip_next_five_minute_start():
    start=dt.datetime(2026,11,28,19,25,tzinfo=autonomous.JST)
    state={'tasks':{'predict-20261128':dict(status='ok',started_at=start.isoformat(),finished_at=(start+dt.timedelta(minutes=3)).isoformat())}}
    assert autonomous.due('predict',start+dt.timedelta(minutes=5),state)
    assert not autonomous.due('predict',start+dt.timedelta(minutes=3),state)


def test_all_result_source_failures_do_not_claim_healthy_success(monkeypatch,tmp_path):
    stub_analysis(monkeypatch)
    now=dt.datetime.now(autonomous.JST)
    metrics=dict(results=dict(attempted=3,saved=0,errors=3,pending=12))
    result=autonomous.operate(tmp_path,'predict',now,lambda *a:metrics)
    assert result['errors']==[dict(phase='predict',error_type='RuntimeError')]
    state=json.loads((tmp_path/'logs/auto_runtime.json').read_text())
    assert state['tasks'][f'predict-{now:%Y%m%d}']['status']=='error'
    assert autonomous.due('predict',now,state)
