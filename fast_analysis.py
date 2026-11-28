"""Small, cached daily audit of saved predictions. Never fit, fetch, send or adopt."""
import csv
import datetime as dt
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import time

from reasoned_forecast import valid_at
from settlement import settle as settle_ticket

VERSION = 'daily-fast-analysis-v1'
AUDIT_SHA256 = '558505ac48cdc1fd676a44c3f679cd37eb50264a4555485ff0614ebbfd27cfdb'
RULE_SHA256 = '54b83a65dd39e2f34d853fc3a46463f955fea44faff98d55c6d2cebbe7f67aed'
ROOT = Path(__file__).resolve().parent
_PURE = None


class AnalysisIntegrityError(ValueError):
    """Inputs changed in a way that must not silently alter an earlier audit."""


def _digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _pure():
    global _PURE
    path = ROOT/'logs/focus_20261128_analyze.py'
    if _digest(path.read_bytes()) != AUDIT_SHA256:
        raise AnalysisIntegrityError('Fixed audit source hash changed')
    if _digest((ROOT/'reasoned_forecast.py').read_bytes()) != RULE_SHA256:
        raise AnalysisIntegrityError('Fixed reasoned forecast source hash changed')
    if _PURE is None:
        spec = importlib.util.spec_from_file_location('_fixed_fast_audit', path)
        _PURE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_PURE)
    return _PURE


def _pick(p):
    return bool(re.fullmatch(r'[1-6]-[1-6]-[1-6]', str(p))) and len(set(p.split('-'))) == 3


def _number(x):
    try:
        value = float(x)
    except (TypeError, ValueError):
        raise AnalysisIntegrityError('Non-numeric probability, odds or payout')
    if not math.isfinite(value):
        raise AnalysisIntegrityError('Non-finite probability, odds or payout')
    return value


def _key(row):
    return f'{row["hd"]}-{int(row["jcd"])}-{int(row["rno"])}'


def _outcomes(text, hd):
    outcomes = {}
    for raw in csv.DictReader(io.StringIO(text)):
        if str(raw.get('hd')) != hd:
            raise AnalysisIntegrityError('Result date differs from requested day')
        payouts = json.loads(raw.get('tri_payouts') or '{}')
        if not isinstance(payouts, dict):
            raise AnalysisIntegrityError('Payouts must be an explicit mapping')
        normalized = {}
        for pick, value in payouts.items():
            number = _number(value)
            if not _pick(pick) or number <= 0 or number != int(number):
                raise AnalysisIntegrityError('Invalid explicit payout')
            normalized[pick] = int(number)
        tri, pay = raw.get('tri', ''), raw.get('tri_pay', '')
        if _pick(tri) and pay:
            number = _number(pay)
            if number > 0 and number == int(number):
                if tri in normalized and normalized[tri] != int(number):
                    raise AnalysisIntegrityError('Conflicting trifecta payout fields')
                normalized[tri] = int(number)
        outcome = dict(race_status=raw.get('race_status', ''), tri_payouts=normalized,
                       refund_boats=''.join(sorted(set(re.findall(r'[1-6]', raw.get('refund_boats', ''))))))
        key = _key(raw)
        if key in outcomes:
            _check_outcome(outcomes[key], outcome)
        outcomes[key] = outcome
    return outcomes


def _final(outcome):
    return bool(outcome and (outcome.get('race_status') == 'cancelled' or
                            outcome.get('race_status') == 'settled' and outcome.get('tri_payouts')))


def _check_outcome(old, current):
    if _final(old) and old != current:
        raise AnalysisIntegrityError('Previously published settled result changed or disappeared')


def _validate(row, manifest):
    if row.get('model_ids') and row['model_ids'] != manifest['baseline_ids']:
        raise AnalysisIntegrityError('Baseline model IDs differ from frozen manifest')
    if not row.get('asof_verified') or not row.get('complete_market'):
        return dict(baseline=False, candidate=False, rules=False, consensus=False)
    if row.get('model_ids') != manifest['baseline_ids']:
        raise AnalysisIntegrityError('Complete snapshot lacks frozen baseline IDs')
    try:
        f, start, seen, close = [dt.datetime.fromisoformat(row[k]) for k in
                                ('features_observed_at', 'odds_started_at', 'observed_at', 'deadline')]
        if not all(v.tzinfo is not None for v in (f, start, seen, close)) or not f <= start <= seen <= close-dt.timedelta(minutes=2):
            return dict(baseline=False, candidate=False, rules=False, consensus=False)
    except (KeyError, TypeError, ValueError):
        return dict(baseline=False, candidate=False, rules=False, consensus=False)

    def probability(mapping):
        if not isinstance(mapping, dict) or len(mapping) != 120 or any(not _pick(k) or not 0 < _number(v) <= 1 for k, v in mapping.items()) or abs(sum(float(v) for v in mapping.values())-1) > 1e-6:
            raise AnalysisIntegrityError('Incomplete probabilities in a complete market')
    probability(row.get('probabilities'))
    odds = row.get('odds')
    if not isinstance(odds, dict) or len(odds) != 120 or any(not _pick(k) or _number(v) <= 0 for k, v in odds.items()):
        raise AnalysisIntegrityError('Incomplete odds in a complete market')
    candidate = False
    if row.get('shadow'):
        shadow = row['shadow']
        saved = shadow.get('manifest', {})
        if saved.get('baseline_ids') != manifest['baseline_ids'] or saved.get('candidate_ids') != manifest['candidate_ids']:
            raise AnalysisIntegrityError('Candidate model IDs differ from frozen manifest')
        if saved != manifest:
            raise AnalysisIntegrityError('Snapshot challenger manifest differs from frozen manifest')
        try:
            created = dt.datetime.fromisoformat(saved['created_at'])
            historical_cutoff = int(saved['trained_before'])
            if created.tzinfo is None or created > seen or historical_cutoff > int(row['hd']):
                raise AnalysisIntegrityError('Candidate was not available at prediction time')
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalysisIntegrityError('Candidate timing proof missing or invalid') from exc
        probability(shadow.get('probabilities'))
        candidate = True
    reasoned = row.get('reasoned')
    if isinstance(reasoned, dict) and reasoned.get('eligible') and reasoned.get('rules_id') != RULE_SHA256:
        raise AnalysisIntegrityError('Recorded reasoning rules changed')
    rules = bool(isinstance(reasoned, dict) and reasoned.get('rules_id') == RULE_SHA256 and
                 valid_at(reasoned, row['features_observed_at'], row['odds_started_at'], row['observed_at'], row['deadline'], 2))
    return dict(baseline=True, candidate=candidate, rules=rules, consensus=rules)


def _profiles():
    profiles = []
    for source in ('baseline', 'candidate'):
        for count in (1, 2, 4):
            for threshold in (.11, .12):
                for ev2 in (.8, 1.):
                    for allocation in (('equal100', 'flat10000', 'dutch10000') if count == 4 else ('equal100',)):
                        profiles.append(dict(source=source, count=count, threshold=threshold, ev2=ev2, allocation=allocation))
    for source, counts in (('rules', (1, 2, 4)), ('consensus', (1, 2, 3, 4))):
        for count in counts:
            for allocation in (('equal100', 'flat10000', 'dutch10000') if count == 4 else ('equal100',)):
                profiles.append(dict(source=source, count=count, threshold=None, ev2=1., allocation=allocation))
    for p in profiles:
        p['profile'] = f'{p["source"]}/{p["count"]}/p{p["threshold"]}/ev2={p["ev2"]}/{p["allocation"]}'
    return profiles


def _select(row, profile, available, pure):
    source, count = profile['source'], profile['count']
    if not available[source]:
        return []
    if source == 'consensus':
        ranked = sorted(row['probabilities'], key=lambda k: (-row['probabilities'][k], k))[:4]
        return [k for k in ranked if k in row['reasoned']['picks'] and row['probabilities'][k] >= .12 and row['probabilities'][k]*row['odds'][k] >= 1.][:count]
    return pure.select(row, source, count, profile['threshold'], profile['ev2'])


def _settle_record(base, amounts, outcome):
    # Per-ticket settlement keeps a partial refund from making other tickets losses.
    evidence = outcome or {}
    if evidence.get('race_status') not in ('settled', 'cancelled'):
        evidence = dict(evidence, tri_payouts={})
    result = [settle_ticket(pick, evidence) for pick in amounts]
    pending = any(r[0] == 'pending' for r in result)
    return dict(base, amounts=amounts, pending=pending, invested=0 if pending else sum(amounts.values()),
                returned=0 if pending else sum(r[2]*amount//100 for r, amount in zip(result, amounts.values())),
                hits=0 if pending else sum(r[1] for r in result),
                refund=0 if pending else sum(amount for r, amount in zip(result, amounts.values()) if r[0] == 'refunded'))


def _atomic(path, text):
    temporary = path.with_name('.'+path.name+'.tmp')
    temporary.write_text(text, encoding='utf8')
    temporary.replace(path)


def run(logs, hd):
    """Audit one day's saved data, returning a summary; immutable failures raise."""
    started = time.perf_counter()
    hd = str(hd)
    if not re.fullmatch(r'\d{8}', hd):
        raise ValueError('hd must be YYYYMMDD')
    dt.datetime.strptime(hd, '%Y%m%d')
    pure = _pure()
    logs = Path(logs)
    folder = logs/'auto_analysis'/hd
    state_path, summary_path, records_path = [folder/n for n in ('state.json', 'summary.json', 'records.csv')]
    market_path, result_path = logs/f'market_{hd}.jsonl', logs/f'results_{hd}.csv'
    market_bytes = market_path.read_bytes() if market_path.exists() else b''
    result_bytes = result_path.read_bytes() if result_path.exists() else b''
    manifest_path = logs.parent/'model/shadow/manifest.json'
    if not manifest_path.exists():
        raise AnalysisIntegrityError('Frozen model manifest missing; analysis left untouched')
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    profiles = _profiles()
    inputs_id = _digest([VERSION, _digest(market_bytes), _digest(result_bytes), _digest(manifest_bytes), _digest(profiles)])
    old = json.loads(state_path.read_text()) if state_path.exists() else None
    if old:
        if old.get('version') != VERSION or old.get('profile_hash') != _digest(profiles) or old.get('manifest_hash') != _digest(manifest_bytes):
            raise AnalysisIntegrityError('Daily audit version, policy or frozen model manifest changed')
        if not summary_path.exists() or not records_path.exists() or _digest(summary_path.read_bytes()) != old.get('summary_hash') or _digest(records_path.read_bytes()) != old.get('records_hash'):
            raise AnalysisIntegrityError('Cached audit artifacts missing or modified')
        if old['inputs_id'] == inputs_id:
            result = json.loads(summary_path.read_text())
            result['metadata'].update(same_input=True, new_input_count=0, new_market_count=0,
                                      new_outcome_count=0, reused_count=len(old['races'])*len(profiles),
                                      elapsed_seconds=round(time.perf_counter()-started, 6))
            return result  # Do not rewrite any file on unchanged input.
    raw = [json.loads(line) for line in market_bytes.decode().splitlines() if line.strip()]
    line_hashes = [_digest(r) for r in raw]
    if old and line_hashes[:len(old['market_line_hashes'])] != old['market_line_hashes']:
        raise AnalysisIntegrityError('Earlier market snapshot changed or disappeared')
    first = {}
    for row in sorted(raw, key=lambda r: r['observed_at']):
        if str(row.get('hd')) != hd:
            raise AnalysisIntegrityError('Market date differs from requested day')
        first.setdefault(_key(row), row)
    outcomes = _outcomes(result_bytes.decode(), hd)
    if old:
        for key, prior in old['outcomes'].items():
            _check_outcome(prior, outcomes.get(key))
        for key, prior in old['races'].items():
            if key not in first or _digest(first[key]) != prior['fingerprint']:
                raise AnalysisIntegrityError('Earlier first snapshot changed or disappeared')
    cache, records = {}, []
    previous = old['races'] if old else {}
    new_keys, changed_outcomes = set(first)-set(previous), set()
    reused, selection_reused, selections, settlements = 0, 0, 0, 0
    available_counts = dict.fromkeys(('baseline', 'candidate', 'rules', 'consensus'), 0)
    for key, row in first.items():
        prior = previous.get(key)
        available = prior['available'] if prior else _validate(row, manifest)
        for source, okay in available.items():
            available_counts[source] += int(okay)
        outcome, old_outcome = outcomes.get(key), old['outcomes'].get(key) if old else None
        outcome_changed = outcome != old_outcome
        if prior and outcome_changed:
            changed_outcomes.add(key)
        decisions, allocation_cache = {}, {}
        for profile in profiles:
            name = profile['profile']
            prior_decision = prior['decisions'][name] if prior else None
            if prior_decision is not None:
                selection_reused += 1
                picks, amounts, record = prior_decision['picks'], prior_decision['amounts'], prior_decision['record']
                if not picks or not outcome_changed or (record and not record['pending'] and _final(old_outcome)):
                    decisions[name] = prior_decision
                    reused += 1
                    if record:
                        records.append(record)
                    continue
            else:
                selections += 1
                picks = _select(row, profile, available, pure)
                allocation_key = (tuple(picks), profile['allocation'])
                if allocation_key not in allocation_cache:
                    allocation_cache[allocation_key] = pure.allocate(picks, row.get('odds', {}), profile['allocation'])
                amounts = allocation_cache[allocation_key]
                if amounts is None:
                    raise AnalysisIntegrityError('Missing allocation odds')
            record = None
            if picks:
                base = dict(profile=name, race=key, day=hd, observed_at=row['observed_at'], picks=' '.join(picks))
                record = _settle_record(base, amounts, outcome)
                settlements += 1
                records.append(record)
            decisions[name] = dict(picks=picks, amounts=amounts, record=record)
        cache[key] = dict(fingerprint=_digest(row), available=available, decisions=decisions)
    summaries = []
    for profile in profiles:
        rows = [r for r in records if r['profile'] == profile['profile']]
        summaries.append(dict(profile, eligible_races=available_counts[profile['source']], **pure.summarize(rows)))
    metadata = dict(version=VERSION, hd=hd, inputs_id=inputs_id, same_input=False,
                    created_at=dt.datetime.now(dt.timezone.utc).isoformat(), market_rows=len(raw), first_market_races=len(first),
                    verified_baseline_races=available_counts['baseline'], verified_candidate_races=available_counts['candidate'],
                    verified_rule_races=available_counts['rules'], profile_count=len(profiles),
                    latest_observed=max((r['observed_at'] for r in first.values()), default=None),
                    new_input_count=len(new_keys | changed_outcomes), new_market_count=len(new_keys),
                    new_outcome_count=len(changed_outcomes), reused_count=reused,
                    selection_reused_count=selection_reused, new_selection_count=selections,
                    new_settlement_count=settlements, elapsed_seconds=round(time.perf_counter()-started, 6),
                    audit_sha256=AUDIT_SHA256, rule_sha256=RULE_SHA256, model_manifest_hash=_digest(manifest_bytes),
                    formal_28_day_gate=False, day_ci_note='One day cannot estimate day-to-day variation; no day-level 95% interval.',
                    scope='Daily exploratory audit only. No forecast generation, fitting, adoption or notification. Historical policy/allocations may be post hoc.')
    result = dict(metadata=metadata, profiles=summaries)
    summary_text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    stream = io.StringIO(); fields = ['profile', 'race', 'day', 'observed_at', 'picks', 'amounts', 'pending', 'invested', 'returned', 'hits', 'refund']
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n'); writer.writeheader()
    for record in records:
        writer.writerow(dict(record, amounts=json.dumps(record['amounts'], separators=(',', ':'))))
    records_text = stream.getvalue()
    state = dict(version=VERSION, inputs_id=inputs_id, profile_hash=_digest(profiles), manifest_hash=_digest(manifest_bytes),
                 market_line_hashes=line_hashes, outcomes=outcomes, races=cache,
                 records_hash=_digest(records_text.encode()), summary_hash=_digest(summary_text.encode()))
    folder.mkdir(parents=True, exist_ok=True)
    _atomic(records_path, records_text); _atomic(summary_path, summary_text)
    _atomic(state_path, json.dumps(state, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n')
    _atomic(logs/'auto_analysis_latest.json', summary_text)
    return result
