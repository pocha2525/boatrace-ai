"""Frozen challenger validation using paired, prospective market observations."""
import datetime
import hashlib
import json
import os
import shutil
from pathlib import Path
import numpy as np
import pandas as pd

TARGETS=('win','top2','top3')


def hashes(folder):
    return [hashlib.sha256((Path(folder)/f'model_{y}.txt').read_bytes()).hexdigest() for y in TARGETS]


def stage(model_dir, candidate_dir, temperature, eligible, trained_before, created_at):
    root=Path(model_dir)/'shadow'
    if (root/'manifest.json').exists():
        return False  # Never replace a challenger while its outcomes accumulate.
    root.mkdir(parents=True,exist_ok=True)
    for y in TARGETS:
        shutil.copy(Path(candidate_dir)/f'model_{y}.txt',root/f'model_{y}.txt')
    manifest=dict(version=1,created_at=created_at,trained_before=int(trained_before),
                  baseline_ids=hashes(model_dir),candidate_ids=hashes(root),temperature=float(temperature),
                  historical_quality_passed=bool(eligible),policy='current_two',
                  min_days=28,min_races=200,rule='paired-day-bootstrap-v1')
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return True


_MODELS={}


def predict_frame(frame, model_dir):
    root=Path(model_dir)/'shadow'
    if not (root/'manifest.json').exists():
        return None
    manifest=json.loads((root/'manifest.json').read_text())
    if hashes(model_dir)!=manifest['baseline_ids'] or hashes(root)!=manifest['candidate_ids']:
        return None
    import lightgbm as lgb
    key=tuple(manifest['candidate_ids'])
    if key not in _MODELS:
        _MODELS[key]=[lgb.Booster(model_file=str(root/f'model_{y}.txt')) for y in TARGETS]
    models=_MODELS[key]
    if any(not set(m.feature_name()).issubset(frame) for m in models):
        return None
    from itertools import permutations
    w,t2,t3=[m.predict(frame[m.feature_name()]) for m in models]
    w=w/w.sum();t2=t2*2/t2.sum();t3=t3*3/t3.sum()
    q2=np.clip(t2-w,.001,None);q3=np.clip(t3-t2,.001,None)
    boats=frame.boat.astype(int).tolist()
    p={f'{boats[a]}-{boats[b]}-{boats[c]}':float(w[a]*q2[b]/(q2.sum()-q2[a])*q3[c]/(q3.sum()-q3[a]-q3[b]))
       for a,b,c in permutations(range(6),3)}
    keys=list(p);values=np.array(list(p.values()));values=values/values.sum()
    values=np.exp(np.log(values)/manifest['temperature']-np.max(np.log(values)/manifest['temperature']))
    values/=values.sum()
    return dict(probabilities=dict(zip(keys,map(float,values))),manifest=manifest)


def assess(detail, manifest):
    base=dict(approved=False,reason='締切前オッズを使う新規収支の比較データ不足',candidate_roi=None,baseline_roi=None)
    if detail.empty or not {'pair_id','model_ids','policy','hd','pending','bets','investment','returned','observed_at'}.issubset(detail):
        return base
    encoded=lambda x: json.dumps(x,separators=(',',':'))
    a=detail[(detail.policy=='current_two')&(detail.model_ids==encoded(manifest['baseline_ids']))]
    b=detail[(detail.policy=='shadow_current_two')&(detail.model_ids==encoded(manifest['candidate_ids']))]
    if a.pair_id.duplicated().any() or b.pair_id.duplicated().any():
        return dict(base,reason='比較対象に重複あり')
    pairs=a.merge(b,on='pair_id',suffixes=('_base','_cand'),validate='one_to_one')
    pairs=pairs[(pairs.observed_at_base>=manifest['created_at'])&(pairs.observed_at_cand>=manifest['created_at'])]
    start=datetime.datetime.fromisoformat(manifest['created_at']).date()+datetime.timedelta(days=1)
    end=start+datetime.timedelta(days=27)
    # A fixed first 28-day block prevents daily optional stopping until a lucky
    # interval passes. Later days never repair a failed evaluation block.
    pairs=pairs[pairs.hd_base.astype(str).between(start.strftime('%Y%m%d'),end.strftime('%Y%m%d'))]
    # Incomplete paired settlement cannot disappear from the comparison.
    if not len(pairs) or (pairs.pending_base+ pairs.pending_cand).gt(0).any():
        return base
    days=sorted(pairs.hd_base.unique())
    min_days=max(28,int(manifest.get('min_days',28)))
    min_races=max(200,int(manifest.get('min_races',200)))
    if len(days)<min_days or (pd.to_datetime(str(days[-1]))-pd.to_datetime(str(days[0]))).days<min_days-1:
        return base
    if (pairs.bets_cand>0).sum()<min_races or (pairs.bets_base>0).sum()<min_races:
        return base
    if not manifest.get('historical_quality_passed'):
        columns=['log_probability_base','log_probability_cand']
        if not set(columns).issubset(pairs):
            return dict(base,reason='新規の確率精度と収支の両方を確認するデータ不足')
        quality_pairs=pairs.dropna(subset=columns)
        if len(quality_pairs)<500:
            return dict(base,reason='新規の確率精度と収支の両方を確認するデータ不足')
        from evaluation import compare
        quality=compare(quality_pairs.log_probability_base,quality_pairs.log_probability_cand,quality_pairs.hd_base,gain=.002)
        if not quality['approved']:
            return dict(base,reason='新規期間の確率精度の改善を確認できない')
    cols=['investment_base','returned_base','investment_cand','returned_cand']
    daily=pairs.groupby('hd_base')[cols].sum()
    if not np.isfinite(daily.to_numpy()).all() or (daily<0).any().any():
        return dict(base,reason='収支データ不正')
    rng=np.random.default_rng(20261128)
    sample=daily.to_numpy()[rng.integers(0,len(daily),size=(3000,len(daily)))].sum(axis=1)
    valid=(sample[:,0]>0)&(sample[:,2]>0)
    if valid.mean()<.99:return base
    sample=sample[valid];rb=sample[:,1]/sample[:,0]*100;rc=sample[:,3]/sample[:,2]*100
    lo=float(np.quantile(rc,.025));gain=float(np.quantile(rc-rb,.025))
    sums=daily.sum();cr=float(sums.returned_cand/sums.investment_cand*100);br=float(sums.returned_base/sums.investment_base*100)
    approved=lo>100 and gain>0
    return dict(approved=bool(approved),reason='新規収支の黒字と基準比改善を確認' if approved else '黒字または改善の確証不足',
                candidate_roi=cr,baseline_roi=br,candidate_low95=lo,gain_low95=gain,days=len(days),paired_races=len(pairs))


def review(model_dir, logs_dir, now):
    root=Path(model_dir)/'shadow';manifest_path=root/'manifest.json'
    if not manifest_path.exists():return dict(approved=False,reason='比較する候補モデル未作成')
    manifest=json.loads(manifest_path.read_text())
    if hashes(model_dir)!=manifest['baseline_ids'] or hashes(root)!=manifest['candidate_ids']:
        return dict(approved=False,reason='モデルが比較開始時と一致しない')
    path=Path(logs_dir)/'policy_replay.csv'
    result=assess(pd.read_csv(path,dtype={'hd':str}) if path.exists() else pd.DataFrame(),manifest)
    Path(logs_dir).mkdir(parents=True,exist_ok=True)
    (Path(logs_dir)/'profit_gate.json').write_text(json.dumps(dict(checked_at=now,**result),ensure_ascii=False,indent=2))
    if not result['approved']:return result
    archive=Path(model_dir)/'archive'/('profit_'+now.replace(':','').replace('-','').replace('+','_'))
    archive.mkdir(parents=True,exist_ok=True)
    for y in TARGETS:
        shutil.copy(Path(model_dir)/f'model_{y}.txt',archive)
    calibration=Path(model_dir)/'calibration.json'
    if calibration.exists():shutil.copy(calibration,archive)
    trained_at=Path(model_dir)/'trained_at.txt'
    if trained_at.exists():shutil.copy(trained_at,archive)
    shutil.copy(manifest_path,archive/'profit_manifest.json')
    for y in TARGETS:shutil.copy(root/f'model_{y}.txt',Path(model_dir)/f'model_{y}.txt')
    calibration.write_text(json.dumps(dict(temperature=manifest['temperature'],trained_before=manifest['trained_before'],profit_evidence=result)))
    trained_at.write_text(f'{now} / 固定候補の28日間収支確認により採用 / 学習対象 {manifest["trained_before"]} より前\n')
    with (Path(model_dir)/'history.csv').open('a') as f:
        moment=datetime.datetime.fromisoformat(now)
        f.write(f'{moment:%Y%m%d%H%M},収支確認更新,ROI {result["baseline_roi"]:.2f}→{result["candidate_roi"]:.2f}\n')
    shutil.rmtree(root)
    return result
