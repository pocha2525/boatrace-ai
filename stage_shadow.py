"""Fit one frozen challenger from past cached data; do not replace live models."""
import datetime
import os
import tempfile
from pathlib import Path
import lightgbm as lgb
import nightly
import train
import profit_gate
from common import MODEL,TARGETS,now_jst


def main():
    if (Path(MODEL)/'shadow'/'manifest.json').exists():
        print('固定候補は既にあります。学習・上書きは行いません。');return
    raw,rc=train.load_all()
    cutoff=int(now_jst().strftime('%Y%m%d'))
    raw=raw[raw.date<cutoff].copy();rc=rc[rc.date<cutoff].copy()
    if raw.date.nunique()<180:raise ValueError('過去成績キャッシュ不足')
    frame=nightly.add_all(nightly.prepare(raw,rc),rc)
    features=lgb.Booster(model_file=str(Path(MODEL)/'model_win.txt')).feature_name()
    features=list(dict.fromkeys(features+['ss_bad']))
    frame=frame[frame.date>=train.TRAIN_FROM]
    with tempfile.TemporaryDirectory() as candidate:
        params=dict(objective='binary',learning_rate=.05,num_leaves=63,
                    bagging_fraction=.8,bagging_freq=1,feature_fraction=.8,
                    verbose=-1,num_threads=2,seed=20261128)
        for y in TARGETS:
            data=lgb.Dataset(frame[features],frame[y],categorical_feature=['venue'])
            model=lgb.train(params,data,num_boost_round=400)
            model.save_model(str(Path(candidate)/f'model_{y}.txt'))
            print(y,'候補学習完了',flush=True)
        profit_gate.stage(MODEL,candidate,1.,False,cutoff,now_jst().isoformat())
    print(f'本番は変更せず、固定候補を作成。材料{len(features)}項目、学習対象〜{frame.date.max()}。収支・確率精度とも新規期間で検証。')

if __name__=='__main__':main()
