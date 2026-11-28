"""Scheduled operation and bounded recovery; no ChatGPT session is required."""
import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
MIN_INTERVAL = 4 * 60


def load(path):
    return json.loads(path.read_text()) if path.exists() else {'tasks': {}}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(path)


def target_day(mode, now):
    day = now.date()-dt.timedelta(days=1) if mode == 'report' and now.hour < 21 else now.date()
    return day.strftime('%Y%m%d')


def phases(mode, now):
    if mode != 'auto':
        return [mode]
    result = []
    if 8 <= now.hour < 11:
        result.append('morning')
    if 8 <= now.hour < 23:
        result.append('predict')
    if now.hour == 23:
        result.extend(['results', 'report'])
    return result


def due(mode, now, state):
    day = target_day(mode, now)
    prior = state.get('tasks', {}).get(f'{mode}-{day}', {})
    if prior.get('status') != 'ok':
        return True
    if mode not in ('predict', 'results'):
        return False
    # Measure cadence from cycle start: a slow previous cycle must not turn
    # a five-minute clock back into ten-minute collection.
    return (now-dt.datetime.fromisoformat(prior.get('started_at') or prior['finished_at'])).total_seconds() >= MIN_INTERVAL


def check(repo, now):
    repo = Path(repo)
    files = [repo/'model'/f'model_{t}.txt' for t in ('win', 'top2', 'top3')]
    if not all(p.exists() for p in files):
        raise RuntimeError('Missing live model files')
    state = load(repo/'logs/auto_runtime.json')
    return dict(checked_at=now.isoformat(), mode='read_only_check',
                model_hashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in files],
                due_phases=[m for m in phases('auto', now) if due(m, now, state)],
                retraining=False, notification_sent=False)


def execute(mode, hd):
    os.environ['HD'] = hd
    os.environ['MANUAL'] = '0'
    if mode == 'results':
        predict = importlib.import_module('predict')
        from common import now_jst
        return predict.update_results(hd, now_jst())
    module = importlib.import_module(mode)
    if mode == 'report':
        predict = importlib.import_module('predict')
        from common import now_jst
        predict.update_results(hd, now_jst())
    return module.main()


def operate(repo, mode, now=None, executor=None):
    now = now or dt.datetime.now(JST)
    repo = Path(repo)
    path = repo/'logs/auto_runtime.json'
    state = load(path)
    state.setdefault('tasks', {})
    executor = executor or execute
    ran, errors = [], []
    for phase in phases(mode, now):
        if not due(phase, now, state):
            continue
        hd = target_day(phase, now)
        key = f'{phase}-{hd}'
        prior = state['tasks'].get(key, {})
        started = dt.datetime.now(JST)
        try:
            metrics = executor(phase, hd)
            result_metrics = metrics.get('results', {}) if isinstance(metrics, dict) else {}
            attempted = result_metrics.get('attempted', 0)
            if attempted and result_metrics.get('saved', 0) == 0 and result_metrics.get('errors', 0) >= attempted:
                raise RuntimeError('All attempted official result sources unavailable')
            finished = dt.datetime.now(JST)
            state['tasks'][key] = dict(status='ok', started_at=started.isoformat(),
                finished_at=finished.isoformat(), consecutive_failures=0,
                metrics=metrics if isinstance(metrics, dict) else None)
            ran.append(phase)
        except Exception as exc:
            # Persist the failure before exit. Unknown notification claims are
            # protected separately by the durable delivery ledger.
            finished = dt.datetime.now(JST)
            failures = int(prior.get('consecutive_failures', 0))+1
            state['tasks'][key] = dict(status='error', started_at=started.isoformat(),
                finished_at=finished.isoformat(), error_type=type(exc).__name__,
                consecutive_failures=failures)
            errors.append(dict(phase=phase, error_type=type(exc).__name__))
            print('AUTONOMOUS_PHASE_ERROR', phase, type(exc).__name__, flush=True)
        state['updated_at'] = dt.datetime.now(JST).isoformat()
        save(path, state)
    # This audit reads saved values only and caches unchanged settled records.
    if ran:
        try:
            import live_stats
            state['live_stats'] = live_stats.run(repo/'logs', now.strftime('%Y%m%d'))
            import fast_analysis
            metrics = fast_analysis.run(repo/'logs', now.strftime('%Y%m%d'))
            state['analysis'] = metrics.get('metadata', metrics) if isinstance(metrics, dict) else {}
            state['tasks'][f'analysis-{now:%Y%m%d}'] = dict(status='ok', finished_at=dt.datetime.now(JST).isoformat(), consecutive_failures=0)
        except Exception as exc:
            errors.append(dict(phase='analysis', error_type=type(exc).__name__))
            state['analysis'] = dict(status='error', error_type=type(exc).__name__)
            key = f'analysis-{now:%Y%m%d}'
            failures = state['tasks'].get(key, {}).get('consecutive_failures', 0)+1
            state['tasks'][key] = dict(status='error', finished_at=dt.datetime.now(JST).isoformat(), error_type=type(exc).__name__, consecutive_failures=failures)
    state['last_run'] = dict(at=dt.datetime.now(JST).isoformat(), ran=ran, errors=errors,
                             retraining=False)
    save(path, state)
    # Report a persistent operational failure once per phase per day, never
    # retransmit an unknown delivery. Ordinary analysis/status stays internal.
    for error in errors:
        key = f'{error["phase"]}-{target_day(error["phase"], now)}'
        attempts = state['tasks'].get(key, {}).get('consecutive_failures', 0)
        if attempts >= 3:
            from common import send_notify
            send_notify('自動運転の継続障害',
                f'{error["phase"]}で{attempts}回連続の失敗。未取得を不的中や0円にせず保留しています。次の定期実行で再確認します。',
                notification_id='runtime-error-'+key)
    print('AUTONOMOUS_RUN', json.dumps(state['last_run'], ensure_ascii=False), flush=True)
    return state['last_run']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['auto','predict','morning','nightly','report','results'], default='auto')
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    now = dt.datetime.now(JST)
    if args.preflight:
        state = load(Path(args.repo)/'logs/auto_runtime.json')
        ready = any(due(m, now, state) for m in phases(args.mode, now))
        if os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch':
            ready = True
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                f.write('run='+str(ready).lower()+'\n')
        print('AUTONOMOUS_PREFLIGHT', ready)
        return
    if args.check_only:
        print(json.dumps(check(args.repo, now), ensure_ascii=False))
        return
    result = operate(args.repo, args.mode, now)
    if result['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
