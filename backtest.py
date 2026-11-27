"""Read-only historical experiment; never promotes or overwrites a live model."""
import datetime
import json
import os
from itertools import permutations
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

import before
import cm
import evaluation
import nightly
import train
from common import MODEL, TARGETS, now_jst

ORDERS = np.array(list(permutations(range(6), 3)))
KEYS = ['date', 'venue', 'race']


def probabilities(models, frame):
    w, t2, t3 = [m.predict(frame[m.feature_name()]).reshape(-1, 6) for m in models]
    w /= w.sum(axis=1, keepdims=True)
    t2 *= 2 / t2.sum(axis=1, keepdims=True)
    t3 *= 3 / t3.sum(axis=1, keepdims=True)
    q2, q3 = np.clip(t2-w, .001, None), np.clip(t3-t2, .001, None)
    a, b, c = ORDERS.T
    p = (w[:, a] * q2[:, b] / (q2.sum(1)[:, None]-q2[:, a]) *
         q3[:, c] / (q3.sum(1)[:, None]-q3[:, a]-q3[:, b]))
    return p / p.sum(1, keepdims=True)


def temper(p, temperature):
    logp = np.log(np.clip(p, 1e-15, 1)) / temperature
    logp -= logp.max(1, keepdims=True)
    p = np.exp(logp)
    return p / p.sum(1, keepdims=True)


def outcomes(frame):
    ranks = frame['rank'].to_numpy().reshape(-1, 6)
    return np.array([next(i for i, order in enumerate(ORDERS)
                         if list(row[order]) == [1, 2, 3]) for row in ranks])


def complete(frame):
    """Exactly six distinct boats and one each of ranks 1..3; ties excluded."""
    def valid(g):
        return (sorted(g.boat.tolist()) == [1, 2, 3, 4, 5, 6] and
                all((g['rank'] == n).sum() == 1 for n in (1, 2, 3)))
    keys = [key for key, group in frame.groupby(KEYS) if valid(group)]
    keep = pd.MultiIndex.from_tuples(keys, names=KEYS)
    return frame.set_index(KEYS).loc[lambda x: x.index.isin(keep)].reset_index().sort_values(KEYS+['boat'])


def metrics(p, truth, pay):
    order = np.argsort(-p, axis=1, kind='stable')
    hit1 = order[:, 0] == truth
    hit2 = (order[:, :2] == truth[:, None]).any(1)
    ll = np.log(np.clip(p[np.arange(len(p)), truth], 1e-15, 1))
    return dict(races=len(p), top1_hit_pct=float(hit1.mean()*100),
                top2_hit_pct=float(hit2.mean()*100), mean_log_probability=float(ll.mean()),
                multiclass_brier=float((np.square(p).sum(1)-2*np.exp(ll)+1).mean()),
                fixed1_roi_pct=float((pay*hit1).sum()/len(p)),
                fixed2_roi_pct=float((pay*hit2).sum()/(len(p)*2))), ll, hit1, order


def main():
    # Use the Actions cache only: do not crawl thousands of pages or notify.
    raw, rc = train.load_all()
    if not len(raw) or raw.date.nunique() < 180:
        raise ValueError('バックテストには成績キャッシュが180日以上必要です')
    end = min(int(raw.date.max()), int((now_jst().date()-datetime.timedelta(days=1)).strftime('%Y%m%d')))
    end_day = datetime.datetime.strptime(str(end), '%Y%m%d').date()
    cut = int((end_day-datetime.timedelta(days=27)).strftime('%Y%m%d'))
    holdout_from = int((end_day-datetime.timedelta(days=13)).strftime('%Y%m%d'))
    raw, rc = raw[raw.date <= end].copy(), rc[rc.date <= end].copy()
    frame = nightly.add_all(nightly.prepare(raw, rc), rc)
    tr = frame[(frame.date >= train.TRAIN_FROM) & (frame.date < cut)]
    ev = complete(frame[(frame.date >= cut) & (frame.date <= end)])
    # K tenji is the published exhibition time, not the race finishing time.
    # Legacy before CSVs lack a tenji column; this fallback is explicit.
    observed = ev[['_ex_course', '_ex_wind', '_ex_wave', 'ex_st', 'tenji']].notna().all(1)
    observed &= ev.groupby(KEYS)['_ex_course'].transform('nunique').eq(6)
    observed = observed.groupby([ev[k] for k in KEYS]).transform('all')
    ev = ev[observed].copy()
    validation, holdout = ev[ev.date < holdout_from], ev[ev.date >= holdout_from]
    if validation.date.nunique() < 3 or holdout.date.nunique() < 5 or len(holdout) < 500*6:
        raise ValueError(f'展示時点を照合できる評価データ不足: validation={len(validation)//6}, holdout={len(holdout)//6}')
    reference = [lgb.Booster(model_file=os.path.join(MODEL, f'model_{y}.txt')) for y in TARGETS]
    baseline = reference[0].feature_name()
    candidates = list(dict.fromkeys(baseline + ['ss_bad']))
    bf_days = int(tr.loc[tr.ex_st.notna(), 'date'].nunique())
    use_bf = bf_days >= 60 and holdout.ex_st.notna().mean() >= .8
    if use_bf:
        candidates = list(dict.fromkeys(candidates + before.BEFORE))
    arms = {'baseline_66_refit': baseline, 'candidate': candidates,
            'candidate_without_comments': [f for f in candidates if f not in cm.CM]}
    root = Path('logs/backtest') / (now_jst().strftime('%Y%m%d_%H%M%S')+'_'+os.environ.get('GITHUB_RUN_ID', 'local'))
    root.mkdir(parents=True, exist_ok=True)
    coverage = []
    for split, data in [('train', tr), ('validation', validation), ('holdout', holdout)]:
        for feature in list(dict.fromkeys(candidates + before.BEFORE)):
            coverage.append(dict(split=split, feature=feature, rows=len(data),
                                 available=int(data[feature].notna().sum()) if feature in data else 0))
    pd.DataFrame(coverage).to_csv(root/'coverage.csv', index=False)
    meta = dict(train_from=int(tr.date.min()), train_until=int(tr.date.max()),
                validation_from=int(validation.date.min()), validation_until=int(validation.date.max()),
                holdout_from=int(holdout.date.min()), holdout_until=int(holdout.date.max()),
                train_rows=len(tr), eligible_evaluation_races=len(ev)//6,
                excluded_evaluation_races=len(complete(frame[frame.date >= cut]))//6-len(ev)//6,
                before_training_days=bf_days, before_features_enabled=bool(use_bf),
                model_promoted=False, note='Historical diagnostic, not a fresh prospective holdout. Historical pre-deadline odds unavailable; fixed-ticket returns are not the live EV strategy. New venue comment collectors lack sufficient verified historical captures. K tenji fallback is the published exhibition time. Ties and incomplete fields excluded.')
    params = dict(objective='binary', learning_rate=.05, num_leaves=63,
                  bagging_fraction=.8, bagging_freq=1, feature_fraction=.8,
                  verbose=-1, num_threads=2, seed=20261127)
    vt, truth = outcomes(validation), outcomes(holdout)
    race = holdout.groupby(KEYS, sort=True).first().reset_index()[KEYS]
    result = race.merge(rc[KEYS+['tri_pay']], on=KEYS, how='left', validate='one_to_one')
    if result.tri_pay.isna().any():
        raise ValueError('評価レースの公式払戻が不足しています')
    pay = result.tri_pay.to_numpy(float)
    summaries, scores, all_rows = {}, {}, []
    for name, features in arms.items():
        models = []
        for y in TARGETS:
            ds = lgb.Dataset(tr[features], tr[y], categorical_feature=['venue'])
            models.append(lgb.train(params, ds, num_boost_round=400))
            print(name, y, 'fit complete', flush=True)
        pv = probabilities(models, validation)
        temperatures = [.85, 1., 1.15, 1.3, 1.6]
        temp = max(temperatures, key=lambda t: np.log(temper(pv, t)[np.arange(len(vt)), vt]).mean())
        p = temper(probabilities(models, holdout), temp)
        stat, ll, hit, order = metrics(p, truth, pay)
        stat.update(features=len(features), temperature=temp,
                    validation_log_probability=float(np.log(temper(pv,temp)[np.arange(len(vt)),vt]).mean()))
        summaries[name], scores[name] = stat, (ll, hit)
        detail = result.copy()
        detail['arm'], detail['log_probability'], detail['top1_hit'] = name, ll, hit.astype(int)
        detail['top1_probability'] = p[np.arange(len(p)), order[:, 0]]
        detail['pick1'] = ['-'.join(str(b+1) for b in ORDERS[i]) for i in order[:, 0]]
        detail['pick2'] = ['-'.join(str(b+1) for b in ORDERS[i]) for i in order[:, 1]]
        all_rows.append(detail)
        print(name, json.dumps(stat), flush=True)
    # Select only with validation, then expose a single holdout comparison.
    chosen = max(['candidate', 'candidate_without_comments'], key=lambda n: summaries[n]['validation_log_probability'])
    check = evaluation.compare(scores['baseline_66_refit'][0], scores[chosen][0], result.date.to_numpy())
    meta.update(chosen_on_validation=chosen, comparison=check, metrics=summaries)
    (root/'summary.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf8')
    pd.DataFrame(summaries).T.to_csv(root/'metrics.csv', index_label='arm')
    pd.concat(all_rows, ignore_index=True).to_csv(root/'races.csv', index=False)
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
