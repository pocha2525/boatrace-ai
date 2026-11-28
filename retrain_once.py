"""One explicitly requested research fit. Live and frozen shadow stay untouched."""
import datetime
import hashlib
import json
import os
from pathlib import Path

import lightgbm as lgb
import pandas as pd

import nightly
import train
from common import MODEL, TARGETS, now_jst


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_lines(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def current_rows(logs, hd, asof):
    """Join first pre-race snapshots to already settled, unambiguous outcomes."""
    logs = Path(logs)
    markets = read_lines(logs / f'market_{hd}.jsonl')
    features = read_lines(logs / f'features_{hd}.jsonl')
    path = logs / f'results_{hd}.csv'
    if not path.exists():
        return pd.DataFrame(), 0
    results = pd.read_csv(path, dtype=str).fillna('')
    outcomes = {(int(r.jcd), int(r.rno)): r for r in results.itertuples()}
    snapshots = {(int(r['jcd']), int(r['rno']), r['observed_at']): r for r in features}
    first, rows = {}, []
    for m in sorted(markets, key=lambda r: r['observed_at']):
        first.setdefault((int(m['jcd']), int(m['rno'])), m)
    for key, m in first.items():
        result = outcomes.get(key)
        f = snapshots.get((*key, m.get('features_observed_at')))
        if result is None or f is None or not m.get('asof_verified') or not m.get('complete_market'):
            continue
        try:
            seen = datetime.datetime.fromisoformat(m['observed_at'])
            features_at = datetime.datetime.fromisoformat(f['observed_at'])
            close = datetime.datetime.fromisoformat(m['deadline'])
            got = datetime.datetime.strptime(hd + result.got_at, '%Y%m%d%H:%M').replace(tzinfo=asof.tzinfo)
            tri = result.tri.split('-')
            payouts = json.loads(result.tri_payouts) if result.tri_payouts else {result.tri: result.tri_pay}
            if not (features_at <= seen <= close-datetime.timedelta(minutes=2) < got <= asof):
                continue
            if result.race_status != 'settled' or result.refund_boats or len(payouts) != 1:
                continue
            if len(tri) != len(set(tri)) or len(tri) != 3 or any(b not in '123456' or len(b) != 1 for b in tri):
                continue
            boats = f['boats']
            if sorted(int(b['boat']) for b in boats) != list(range(1, 7)):
                continue
            for b in boats:
                boat = str(int(b['boat']))
                row = dict(b, date=int(hd), race=key[1], venue=key[0])
                row.update(win=int(boat == tri[0]), top2=int(boat in tri[:2]), top3=int(boat in tri))
                rows.append(row)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return pd.DataFrame(rows), len(rows)//6


def main():
    asof = now_jst()
    hd = asof.strftime('%Y%m%d')
    output = Path(os.environ.get('RETRAIN_OUT', 'research_model'))
    if output.exists():
        raise ValueError('Research output already exists; refusing duplicate training')
    protected_paths = [p for p in Path(MODEL).rglob('*') if p.is_file()]
    protected = {str(p): sha(p) for p in protected_paths}
    raw, rc = train.load_all()
    cutoff = int(hd)
    raw, rc = raw[raw.date < cutoff].copy(), rc[rc.date < cutoff].copy()
    if raw.date.nunique() < 180:
        raise ValueError('Cached history has fewer than 180 dates; no mass download or fit')
    frame = nightly.add_all(nightly.prepare(raw, rc), rc)
    frame = frame[frame.date >= train.TRAIN_FROM].copy()
    history_through, history_days = int(raw.date.max()), int(raw.date.nunique())
    del raw, rc
    features = lgb.Booster(model_file=str(Path(MODEL)/'model_win.txt')).feature_name()
    features = list(dict.fromkeys(features + ['ss_bad']))
    live, live_races = current_rows('logs', hd, asof)
    columns = list(dict.fromkeys(features + list(TARGETS) + ['date']))
    frame = pd.concat([frame.reindex(columns=columns), live.reindex(columns=columns)], ignore_index=True)
    for name in features:
        if name not in frame:
            frame[name] = float('nan')
    output.mkdir(parents=True)
    params = dict(objective='binary', learning_rate=.05, num_leaves=63,
                  bagging_fraction=.8, bagging_freq=1, feature_fraction=.8,
                  verbose=-1, num_threads=2, seed=20261128)
    trained = {}
    print('REQUESTED_RETRAIN_START', asof.isoformat(), 'rows', len(frame), 'fresh_settled_races', live_races, flush=True)
    for target in TARGETS:
        dataset = lgb.Dataset(frame[features], frame[target], categorical_feature=['venue'], free_raw_data=True)
        fitted = lgb.train(params, dataset, num_boost_round=400)
        path = output/f'model_{target}.txt'
        fitted.save_model(str(path))
        trained[target] = sha(path)
        del fitted, dataset
        print('RETRAIN_TARGET_COMPLETE', target, trained[target], flush=True)
    if protected != {str(p): sha(p) for p in protected_paths}:
        raise ValueError('Protected model files changed')
    inputs = [Path('logs')/f'{prefix}_{hd}.{ext}' for prefix, ext in
              [('features', 'jsonl'), ('market', 'jsonl'), ('results', 'csv')]]
    manifest = dict(version='requested-retrain-v1', requested=True, source_commit=os.environ.get('GITHUB_SHA'),
                    run_id=os.environ.get('GITHUB_RUN_ID'), started_at=asof.isoformat(),
                    completed_at=now_jst().isoformat(), research_only=True, live_unchanged=True,
                    frozen_shadow_unchanged=True, historical_through=history_through,
                    train_through=int(frame.date.max()), rows=len(frame), historical_days=history_days,
                    fresh_settled_races=live_races, features=features, parameters=params, rounds=400,
                    candidate_hashes=trained, protected_hashes=protected,
                    input_hashes={str(p): sha(p) for p in inputs if p.exists()},
                    evaluation='Not evaluated. Training rows cannot be used for independent performance claims.')
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print('RETRAIN_COMPLETE', json.dumps({k: v for k, v in manifest.items() if k not in ('features','protected_hashes')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
