"""One-use future holdouts and uncertainty for challenger promotion."""
import json
import os
import numpy as np


def fresh_rows(frame, state_path, holdout_from):
    used = 0
    if os.path.exists(state_path):
        with open(state_path, encoding='utf-8') as f:
            used = int(json.load(f).get('used_until', 0))
    return frame[(frame.date >= holdout_from) & (frame.date > used)]


def compare(champ, candidate, dates, min_races=500, gain=.002):
    """Bootstrap days together, so same-day conditions are not independent."""
    a, b, dates = np.asarray(champ), np.asarray(candidate), np.asarray(dates)
    if len(a) != len(b) or len(a) != len(dates) or not len(a):
        return dict(approved=False, reason='比較対象がそろわない', low95=None, high95=None)
    diff = b-a
    days = np.unique(dates)
    rng = np.random.default_rng(20261127)
    sums = np.array([diff[dates == d].sum() for d in days])
    counts = np.array([(dates == d).sum() for d in days])
    sampled = rng.integers(0, len(days), size=(1000, len(days)))
    means = sums[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    lo, hi = np.quantile(means, [.025, .975])
    enough = len(diff) >= min_races and len(days) >= 5
    return dict(approved=bool(enough and lo >= gain),
                reason='新規評価期間・改善の下限を確認' if enough and lo >= gain else
                       '新規評価件数不足または改善の確証不足',
                low95=float(lo), high95=float(hi))


def consume(path, last):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'used_until': int(last)}, f)
