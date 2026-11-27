import json
import datetime
import pandas as pd
import profit_gate


def manifest():
    return dict(created_at='2026-11-01T12:00:00+09:00',baseline_ids=['b'],candidate_ids=['c'],historical_quality_passed=True,min_days=28,min_races=200)


def records(returned=250):
    rows=[]
    for i in range(28):
        hd=(datetime.date(2026,11,2)+datetime.timedelta(days=i)).strftime('%Y%m%d')
        for race in range(8):
            for policy,ids,ret in [('current_two',['b'],180),('shadow_current_two',['c'],returned)]:
                rows.append(dict(hd=hd,pair_id=f'{hd}-{race}',observed_at=f'{hd[:4]}-{hd[4:6]}-{hd[6:]}T12:00:00+09:00',policy=policy,model_ids=json.dumps(ids,separators=(',',':')),pending=0,bets=2,investment=200,returned=ret))
    return pd.DataFrame(rows)


def test_profit_gate_requires_black_and_improved_paired_returns():
    assert profit_gate.assess(records(),manifest())['approved']
    assert not profit_gate.assess(records(190),manifest())['approved']
    d=records();d.loc[d.policy.eq('current_two'),'returned']=300
    assert not profit_gate.assess(d,manifest())['approved']


def test_unresolved_small_changed_or_unpaired_evidence_never_promotes():
    d=records()
    assert not profit_gate.assess(d.head(100),manifest())['approved']
    d.loc[0,'pending']=1
    assert not profit_gate.assess(d,manifest())['approved']
    d=records();d.loc[d.policy.eq('shadow_current_two'),'model_ids']='["other"]'
    assert not profit_gate.assess(d,manifest())['approved']
    m=manifest();m['historical_quality_passed']=False
    assert not profit_gate.assess(records(),m)['approved']


def test_later_lucky_days_do_not_replace_failed_first_block():
    d=records(190);late=records(10000);late.hd='20261230';late.observed_at='2026-12-30T12:00:00+09:00';late.pair_id='late-'+late.pair_id
    assert not profit_gate.assess(pd.concat([d,late],ignore_index=True),manifest())['approved']


def test_stage_is_immutable_and_hashes_exact_model_files(tmp_path):
    current=tmp_path/'model';candidate=tmp_path/'candidate';current.mkdir();candidate.mkdir()
    for y in profit_gate.TARGETS:
        (current/f'model_{y}.txt').write_text('old')
        (candidate/f'model_{y}.txt').write_text('new')
    assert profit_gate.stage(current,candidate,1.1,True,20261101,'2026-11-01T12:00:00+09:00')
    m=json.loads((current/'shadow'/'manifest.json').read_text())
    assert m['candidate_ids']==profit_gate.hashes(candidate)
    (candidate/'model_win.txt').write_text('replacement')
    assert not profit_gate.stage(current,candidate,1.2,True,20261102,'2026-11-02T12:00:00+09:00')
    assert profit_gate.hashes(current/'shadow')==m['candidate_ids']


def test_review_promotes_exact_frozen_files_and_retains_recovery(monkeypatch,tmp_path):
    model=tmp_path/'model';candidate=tmp_path/'candidate';logs=tmp_path/'logs'
    model.mkdir();candidate.mkdir();logs.mkdir()
    for y in profit_gate.TARGETS:
        (model/f'model_{y}.txt').write_text('old')
        (candidate/f'model_{y}.txt').write_text('new')
    profit_gate.stage(model,candidate,1.1,True,20261001,'2026-11-01T12:00:00+09:00')
    (logs/'policy_replay.csv').write_text('hd\n20261102\n')
    monkeypatch.setattr(profit_gate,'assess',lambda *_:dict(approved=True,reason='fixture',baseline_roi=90,candidate_roi=110))
    assert profit_gate.review(model,logs,'2026-12-01T05:00:00+09:00')['approved']
    assert profit_gate.hashes(model)==profit_gate.hashes(candidate)
    assert not (model/'shadow').exists()
    arc=list((model/'archive').iterdir())[0]
    assert (arc/'model_win.txt').read_text()=='old'
    assert json.loads((model/'calibration.json').read_text())['temperature']==1.1


def test_shadow_prediction_and_market_report_share_exact_epoch(tmp_path):
    import datetime
    import policy_replay
    model=tmp_path/'model';candidate=tmp_path/'candidate';logs=tmp_path/'logs'
    model.mkdir();candidate.mkdir();logs.mkdir()
    for y in profit_gate.TARGETS:
        (model/f'model_{y}.txt').write_text('old')
        (candidate/f'model_{y}.txt').write_text('new')
    profit_gate.stage(model,candidate,1.,True,20261001,'2026-11-01T12:00:00+09:00')
    class Uniform:
        def feature_name(self):return ['boat']
        def predict(self,frame):return __import__('numpy').ones(len(frame))
    profit_gate._MODELS[tuple(profit_gate.hashes(candidate))]=[Uniform()]*3
    prediction=profit_gate.predict_frame(pd.DataFrame({'boat':[1,2,3,4,5,6]}),model)
    assert len(prediction['probabilities'])==120
    assert abs(sum(prediction['probabilities'].values())-1)<1e-10
    now=datetime.datetime.fromisoformat('2026-11-02T12:00:00+09:00')
    p=dict(prediction['probabilities']);p['1-2-3']=.2
    scale=sum(v for k,v in p.items() if k!='1-2-3')
    p={k:v*.8/scale if k!='1-2-3' else .2 for k,v in p.items()}
    odds={k:6. for k in p}
    row=policy_replay.capture(logs,'20261102',1,1,now+datetime.timedelta(minutes=5),now,now,p,odds,profit_gate.hashes(model),{},shadow=prediction)
    assert 'shadow' in row
    result=lambda _:pd.DataFrame([dict(jcd=1,rno=1,tri='1-2-3',tri_pay=600,refund_boats='')])
    policy_replay.report(logs,result,'20261102')
    detail=pd.read_csv(logs/'policy_replay.csv')
    assert 'shadow_current_two' in set(detail.policy)
    assert detail.pair_id.nunique()==1
    assert not profit_gate.assess(detail, prediction['manifest'])['approved']


def test_past_quality_failure_can_only_be_overcome_by_fresh_quality_and_profit():
    m=manifest();m['historical_quality_passed']=False
    parts=[]
    for n in range(3):
        d=records();d.pair_id=d.pair_id+f'-{n}';d['log_probability']=-4.
        d.loc[d.policy.eq('shadow_current_two'),'log_probability']=-3.9
        parts.append(d)
    assert profit_gate.assess(pd.concat(parts,ignore_index=True),m)['approved']
    d=pd.concat(parts,ignore_index=True);d['log_probability']=-4.
    assert not profit_gate.assess(d,m)['approved']


def test_manual_training_cannot_bypass_return_validation(monkeypatch,tmp_path):
    import train
    import stage_shadow
    for y in profit_gate.TARGETS:
        (tmp_path/f'model_{y}.txt').write_text('live')
    monkeypatch.setattr(train,'MODEL',str(tmp_path))
    called=[]
    monkeypatch.setattr(stage_shadow,'main',lambda:called.append('shadow'))
    monkeypatch.setattr(train,'download_all',lambda:(_ for _ in ()).throw(AssertionError('legacy training path')))
    train.main()
    assert called==['shadow']
    assert (tmp_path/'model_win.txt').read_text()=='live'
