"""No network, models, training or delivery: only saved-input auditing."""
import copy
import csv
import datetime as dt
import io
from itertools import permutations
import json
from pathlib import Path

import pytest

import fast_analysis as fa

HD = '20261128'


def probabilities():
    picks = ['-'.join(map(str, p)) for p in permutations(range(1, 7), 3)]
    values = dict.fromkeys(picks, .46/116)
    values.update({'1-2-3': .15, '1-3-2': .14, '1-2-4': .13, '1-4-2': .12})
    return values


def fixture(tmp_path):
    logs = tmp_path/'logs'; logs.mkdir()
    manifest = dict(version=1, created_at='2026-11-28T07:10:00+09:00', trained_before=20261128,
                    baseline_ids=[c*64 for c in 'abc'], candidate_ids=[c*64 for c in 'def'],
                    temperature=1., historical_quality_passed=False, policy='current_two', min_days=28,
                    min_races=200, rule='paired-day-bootstrap-v1')
    folder = tmp_path/'model/shadow'; folder.mkdir(parents=True)
    (folder/'manifest.json').write_text(json.dumps(manifest))
    p = probabilities()
    rule = dict(eligible=True, version='reasoned-four-v1', rules_id=fa.RULE_SHA256,
                observed_at='2026-11-28T07:59:01+09:00',
                picks=['1-2-3', '1-3-2', '1-2-4', '1-4-2'])
    row = dict(hd=HD, jcd=18, rno=1, deadline='2026-11-28T08:10:00+09:00',
               features_observed_at='2026-11-28T07:59:00+09:00',
               odds_started_at='2026-11-28T08:00:00+09:00', observed_at='2026-11-28T08:00:01+09:00',
               asof_verified=True, complete_market=True, model_ids=manifest['baseline_ids'],
               probabilities=p, odds=dict.fromkeys(p, 10.),
               shadow=dict(probabilities=p, manifest=manifest), reasoned=rule)
    write_market(logs, [row])
    return logs, row


def write_market(logs, rows):
    (logs/f'market_{HD}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))


def result(rno=1, status='settled', refund='', tri='1-2-3', pay=1200):
    return dict(hd=HD, jcd=18, rno=rno, race_status=status, refund_boats=refund,
                tri=tri, tri_pay=pay or '', tri_payouts=json.dumps({tri: pay}) if tri and pay else '{}')


def write_results(logs, rows):
    stream = io.StringIO(); fields = list(result())
    writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    (logs/f'results_{HD}.csv').write_text(stream.getvalue())


def profile(summary, source='baseline', count=1, threshold=.12, ev2=1., allocation='equal100'):
    return next(r for r in summary['profiles'] if r['source'] == source and r['count'] == count and
                r['threshold'] == threshold and r['ev2'] == ev2 and r['allocation'] == allocation)


def files(logs):
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in logs.rglob('*') if p.is_file() and
            ('auto_analysis' in str(p))}


def test_saved_predictions_settle_and_unchanged_inputs_do_no_work_or_writes(tmp_path, monkeypatch):
    logs, _ = fixture(tmp_path); write_results(logs, [result()])
    first = fa.run(logs, HD)
    p = profile(first)
    assert (p['selected'], p['settled'], p['hits'], p['investment'], p['returned'], p['roi_pct']) == (1, 1, 1, 100, 1200, 1200.)
    assert first['metadata']['formal_28_day_gate'] is False and p['day_ci95'] is None
    assert len(first['profiles']) == 51
    before = files(logs)
    monkeypatch.setattr(fa, '_select', lambda *a: pytest.fail('unchanged input must not select'))
    monkeypatch.setattr(fa, '_settle_record', lambda *a: pytest.fail('unchanged input must not settle'))
    again = fa.run(logs, HD)
    assert again['metadata']['same_input'] and again['metadata']['new_input_count'] == 0
    assert again['metadata']['reused_count'] == 51
    assert files(logs) == before


def test_append_only_new_race_reuses_all_previous_decisions_and_settlement(tmp_path, monkeypatch):
    logs, row = fixture(tmp_path); write_results(logs, [result()]); fa.run(logs, HD)
    second = copy.deepcopy(row); second['rno'] = 2
    second['observed_at'] = '2026-11-28T08:01:00+09:00'
    write_market(logs, [row, second])
    select, settle = fa._select, fa._settle_record
    selected_races, settled_races = [], []
    def choose(r, *args):
        selected_races.append(r['rno']); return select(r, *args)
    def score(base, *args):
        settled_races.append(base['race']); return settle(base, *args)
    monkeypatch.setattr(fa, '_select', choose); monkeypatch.setattr(fa, '_settle_record', score)
    out = fa.run(logs, HD)
    assert selected_races == [2]*51 and all(r.endswith('-2') for r in settled_races)
    assert out['metadata']['new_market_count'] == 1 and out['metadata']['reused_count'] == 51
    p = profile(out)
    assert (p['selected'], p['settled'], p['pending'], p['investment'], p['returned']) == (2, 1, 1, 100, 1200)


def test_pending_to_settled_only_scores_cached_choices(tmp_path, monkeypatch):
    logs, _ = fixture(tmp_path)
    first = fa.run(logs, HD)
    assert profile(first)['pending'] == 1 and profile(first)['investment'] == 0
    write_results(logs, [result()])
    monkeypatch.setattr(fa, '_select', lambda *a: pytest.fail('pending resolution must use saved choices'))
    done = fa.run(logs, HD)
    assert done['metadata']['new_outcome_count'] == 1
    assert done['metadata']['selection_reused_count'] == 51
    assert profile(done)['settled'] == 1 and profile(done)['returned'] == 1200


def test_partial_refund_progresses_to_settled_without_turning_unknown_tickets_into_losses(tmp_path, monkeypatch):
    logs, _ = fixture(tmp_path)
    write_results(logs, [result(status='pending', refund='3', tri='', pay=0)])
    pending = fa.run(logs, HD)
    two = profile(pending, count=2)
    four = profile(pending, count=4)
    assert two['settled'] == 1 and two['investment'] == two['returned'] == 200
    assert four['pending'] == 1 and four['investment'] == 0
    write_results(logs, [result(refund='3', tri='1-2-4', pay=500)])
    monkeypatch.setattr(fa, '_select', lambda *a: pytest.fail('refund progression must retain chosen picks'))
    settled = fa.run(logs, HD)
    four = profile(settled, count=4)
    assert four['settled'] == 1 and four['investment'] == 400 and four['returned'] == 700
    assert four['hits'] == 1


def test_cancelled_race_refunds_all_stakes_without_a_hit(tmp_path):
    logs, _ = fixture(tmp_path); write_results(logs, [result(status='cancelled', tri='', pay=0)])
    out = fa.run(logs, HD)
    for p in out['profiles']:
        assert p['returned'] == p['investment'] and p['hits'] == 0 and p['roi_pct'] == 100.


@pytest.mark.parametrize('kind', ['snapshot', 'removed_snapshot', 'outcome', 'manifest'])
def test_immutable_data_revision_stops_without_overwriting_audit(tmp_path, kind):
    logs, row = fixture(tmp_path); write_results(logs, [result()]); fa.run(logs, HD)
    before = files(logs)
    if kind == 'snapshot':
        row['odds']['1-2-3'] = 20.; write_market(logs, [row])
    elif kind == 'removed_snapshot':
        write_market(logs, [])
    elif kind == 'outcome':
        write_results(logs, [result(pay=1300)])
    else:
        path = logs.parent/'model/shadow/manifest.json'
        m = json.loads(path.read_text()); m['candidate_ids'][0] = 'x'*64; path.write_text(json.dumps(m))
    with pytest.raises(fa.AnalysisIntegrityError):
        fa.run(logs, HD)
    assert files(logs) == before


def test_first_incomplete_snapshot_never_uses_a_later_successful_one(tmp_path):
    logs, row = fixture(tmp_path)
    invalid = copy.deepcopy(row); invalid.update(asof_verified=False, complete_market=False)
    later = copy.deepcopy(row); later['observed_at'] = '2026-11-28T08:01:00+09:00'
    write_market(logs, [invalid, later]); write_results(logs, [result()])
    out = fa.run(logs, HD)
    assert out['metadata']['first_market_races'] == 1
    assert all(p['selected'] == 0 for p in out['profiles'])


def test_wrong_model_identity_and_fixed_audit_hash_fail_closed(tmp_path, monkeypatch):
    logs, row = fixture(tmp_path)
    row['model_ids'] = ['wrong']*3; write_market(logs, [row])
    with pytest.raises(fa.AnalysisIntegrityError, match='Baseline model IDs'):
        fa.run(logs, HD)
    monkeypatch.setattr(fa, 'AUDIT_SHA256', '0'*64)
    with pytest.raises(fa.AnalysisIntegrityError, match='Fixed audit source hash'):
        fa.run(logs, HD)


def test_invalid_reasoning_timing_does_not_invent_rule_or_consensus_picks(tmp_path):
    logs, row = fixture(tmp_path)
    row['reasoned']['observed_at'] = '2026-11-28T08:00:30+09:00'; write_market(logs, [row])
    write_results(logs, [result()]); out = fa.run(logs, HD)
    assert profile(out)['selected'] == 1
    assert all(p['selected'] == 0 for p in out['profiles'] if p['source'] in ('rules', 'consensus'))


def test_four_point_budget_and_dutch_allocation_are_exact(tmp_path):
    logs, _ = fixture(tmp_path); write_results(logs, [result()]); out = fa.run(logs, HD)
    for source in ('baseline', 'candidate', 'rules', 'consensus'):
        threshold = .12 if source in ('baseline', 'candidate') else None
        for allocation in ('flat10000', 'dutch10000'):
            p = profile(out, source, 4, threshold, 1., allocation)
            assert p['investment'] == 10000 and p['selected'] == 1
    records = list(csv.DictReader((logs/'auto_analysis'/HD/'records.csv').open()))
    for r in records:
        amounts = json.loads(r['amounts'])
        assert all(amount % 100 == 0 for amount in amounts.values())


def test_drawdown_and_largest_hit_exclusion_use_whole_race(tmp_path):
    logs, first = fixture(tmp_path)
    second = copy.deepcopy(first); second['rno'] = 2
    second['observed_at'] = '2026-11-28T08:01:00+09:00'
    write_market(logs, [first, second])
    write_results(logs, [result(tri='6-5-4', pay=9000), result(rno=2)])
    p = profile(fa.run(logs, HD))
    assert (p['selected'], p['hits'], p['investment'], p['returned']) == (2, 1, 200, 1200)
    assert p['max_drawdown'] == 100 and p['roi_without_largest_hit'] == 0.
    assert p['largest_hit'].endswith('-2') and p['day_ci95'] is None
