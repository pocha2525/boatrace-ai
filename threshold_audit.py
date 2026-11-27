"""Exploratory threshold/ticket audit. Never updates production settings/models.

The same validation block has already selected temperature, and historical
outcomes have been inspected. A chronological comparison limits hindsight but
is NOT a fresh, untouched prospective test. No historical EV filter is claimed.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

THRESHOLDS = np.round(np.arange(.02, .30001, .005), 6)
TICKETS = (1, 2, 4)
MIN_SELECTION_RACES = 50


def score(frame, threshold, tickets, bootstrap=1000):
    if tickets not in TICKETS:
        raise ValueError('Unsupported ticket count')
    required = ['pick'+str(i) for i in range(1, tickets+1)]
    if not set(required).issubset(frame):
        raise ValueError('Saved predictions lack required ranked picks')
    frame = frame.sort_values(['date', 'venue', 'race'])
    if frame[['date', 'venue', 'race']].duplicated().any():
        raise ValueError('Duplicate evaluation race')
    if frame[required].apply(lambda row: len(set(row)) != tickets, axis=1).any():
        raise ValueError('Ranked picks must be distinct')
    selected = frame.top1_probability.ge(threshold)
    hit = frame[required].eq(frame.tri, axis=0).any(axis=1)
    returns = frame.tri_pay.where(hit & selected, 0.).to_numpy(float)
    investment = selected.to_numpy(float)*100*tickets
    n = int(selected.sum())
    total, spent = float(returns.sum()), float(investment.sum())
    profit = returns-investment
    cumulative = np.r_[0., np.cumsum(profit)]
    max_drawdown = float((np.maximum.accumulate(cumulative)-cumulative).max())
    days = pd.DataFrame(dict(date=frame.date.to_numpy(), returned=returns, spent=investment)).groupby('date').sum()
    roi = total/spent*100 if spent else None
    low = high = None
    if spent and bootstrap:
        rng = np.random.default_rng(20261127)
        indices = rng.integers(0, len(days), size=(bootstrap, len(days)))
        ret = days.returned.to_numpy()[indices].sum(axis=1)
        cost = days.spent.to_numpy()[indices].sum(axis=1)
        defined = cost > 0
        if defined.mean() >= .95:
            low, high = map(float, np.quantile(ret[defined]/cost[defined]*100, [.025, .975]))
    best = int(np.argmax(returns)) if n else None
    without_best = ((total-returns[best])/(spent-investment[best])*100
                    if n > 1 else None)
    active_days = int((days.spent > 0).sum())
    return dict(threshold_pct=round(threshold*100, 3), tickets=tickets, races=n,
                hits=int((hit & selected).sum()), hit_pct=float(hit[selected].mean()*100) if n else None,
                investment=spent, returned=total, profit=total-spent, roi_pct=roi,
                low95_roi_pct=low, high95_roi_pct=high, roi_without_best_race_pct=without_best,
                max_drawdown=max_drawdown, active_days=active_days,
                eligible_for_selection=bool(n >= MIN_SELECTION_RACES and active_days >= 5))


def choose(validation_scores):
    # Maximise a conservative bound only on the earlier validation dates.
    eligible = [r for r in validation_scores if r['eligible_for_selection'] and r['low95_roi_pct'] is not None]
    return max(eligible, key=lambda r: (r['low95_roi_pct'], r['races'], -r['threshold_pct'])) if eligible else None


def audit(root):
    root = Path(root)
    meta = json.loads((root/'summary.json').read_text(encoding='utf8'))
    validation = pd.read_csv(root/'validation_races.csv')
    holdout = pd.read_csv(root/'races.csv')
    if validation.date.max() >= holdout.date.min():
        raise ValueError('Threshold tuning dates overlap evaluation dates')
    grid, choices = [], []
    for arm, group in holdout.groupby('arm', sort=False):
        val = validation[validation.arm.eq(arm)]
        for tickets in TICKETS:
            val_stats = []
            for threshold in THRESHOLDS:
                v = score(val, threshold, tickets)
                h = score(group, threshold, tickets)
                val_stats.append(v)
                for split, result in [('validation', v), ('holdout', h)]:
                    grid.append(dict(arm=arm, split=split, input_mode=meta['input_mode'], **result))
            selected = choose(val_stats)
            if selected is None:
                choices.append(dict(arm=arm, tickets=tickets, selected_threshold_pct=None,
                                    reason='選定用の50レース・5日以上のデータ不足', profit_evidence=False))
                continue
            scored = score(group, selected['threshold_pct']/100, tickets)
            reference = score(group, .12, tickets)
            robust = (scored['eligible_for_selection'] and scored['low95_roi_pct'] is not None
                      and scored['low95_roi_pct'] > 100 and scored['roi_without_best_race_pct'] > 100)
            choices.append(dict(arm=arm, tickets=tickets, selected_threshold_pct=selected['threshold_pct'],
                                validation=selected, holdout=scored, reference12=reference,
                                profit_evidence=bool(robust),
                                reason='過去探索内の参考値。独立した将来データでの再確認が必要'))
    pd.DataFrame(grid).to_csv(root/'threshold_grid.csv', index=False)
    result = dict(holdout_from=meta['holdout_from'], holdout_until=meta['holdout_until'],
                  input_mode=meta['input_mode'], thresholds_pct=[float(t*100) for t in THRESHOLDS],
                  tickets=list(TICKETS), min_validation_races=MIN_SELECTION_RACES,
                  chosen_only_on_validation=True, exploratory=True, model_promoted=False,
                  caveats=['既に結果を見た過去期間の探索。将来の未使用評価ではない',
                           '校正温度と閾値に同じvalidation期間を使用',
                           '信頼区間は日単位bootstrapの各条件別区間で、多重比較補正なし',
                           '締切前オッズがないため期待値条件は再現せず、各点100円固定',
                           '枠なり仮定の期間と展示照合済の期間は同条件として合算しない'],
                  choices=choices)
    (root/'threshold_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return result


def notification(audits):
    labels = {'baseline_66_refit': '基準66', 'candidate': '追加67', 'candidate_recent': '直近81'}
    lines = ['本命確率2〜30%（0.5刻み）×1・2・4点を比較（各100円）。']
    for data in sorted(audits, key=lambda a: a['holdout_from']):
        lines.append(f"{data['holdout_from']}〜{data['holdout_until']}（{'展示照合済' if data['input_mode']=='observed' else '枠なり仮定'}）")
        # The model itself is not selected after seeing this holdout.
        for c in data['choices']:
            if c['arm'] not in {'baseline_66_refit', 'candidate'}:
                continue
            if c.get('holdout'):
                h = c['holdout']
                roi = f"{h['roi_pct']:.1f}%" if h['roi_pct'] is not None else '対象なし'
                lines.append(f"{labels[c['arm']]} {c['tickets']}点: 前期間選定{c['selected_threshold_pct']:g}%以上→後期間{h['races']}R、回収{roi}")
    lines.append('※これは過去探索です。各区間50R・5日以上を選定条件とし、検証後に良かった閾値を本番に自動反映していません。期待値条件は未再現。')
    return '\n'.join(lines)
