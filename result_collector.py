"""Bounded official-result collection with durable retry fairness.

No prediction or settlement is generated here. Unknown results remain unknown;
confirmed payout records are never replaced by a later incomplete response.
"""
import datetime
import json
import math
import os
from pathlib import Path
import re

BACKOFF_MIN = 5
BACKOFF_MAX_MIN = 120


def _text(value):
    return '' if value is None or str(value).lower() == 'nan' else str(value)


def _positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (ValueError, TypeError):
        return False


def confirmed(row):
    """Only actual positive trifecta payouts finish collection of a race."""
    status = _text(row.get('race_status', ''))
    if status not in ('', 'settled'):
        return False
    tri = _text(row.get('tri', ''))
    if not re.fullmatch(r'[1-6]-[1-6]-[1-6]', tri) or len(set(tri.split('-'))) != 3:
        return False
    if _positive(row.get('tri_pay')):
        return True
    payouts = row.get('tri_payouts', {})
    if isinstance(payouts, str):
        try:
            payouts = json.loads(payouts)
        except (ValueError, TypeError):
            payouts = {}
    return isinstance(payouts, dict) and _positive(payouts.get(tri))


def _load(path, hd):
    try:
        state = json.loads(path.read_text(encoding='utf8'))
        if state.get('hd') == hd and isinstance(state.get('races'), dict):
            return state
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return dict(version=1, hd=hd, races={})


def _save(path, state, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    state['updated_at'] = now.isoformat()
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    os.replace(temporary, path)


def _stamp(value, fallback):
    try:
        parsed = datetime.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=fallback.tzinfo)
        return parsed
    except (ValueError, TypeError):
        return fallback


def collect(hd, now, ctx):
    """Collect up to RESULT_MAX due races, least recently attempted first.

    ``ctx`` is the predict module, passed by the caller to keep result parsing,
    network access and CSV ownership in the existing application.
    """
    hd = str(hd)
    metrics = dict(attempted=0, saved=0, settled=0, pending=0, due=0, errors=0, fetched=0, backoff=0)
    notices = ctx.read_log(ctx.notify_path(hd))
    if notices is None or notices.empty:
        return metrics
    got = set()
    results = ctx.read_log(ctx.result_path(hd))
    if results is not None:
        for row in results.to_dict('records'):
            if confirmed(row):
                try:
                    got.add((int(row['jcd']), int(row['rno'])))
                except (KeyError, TypeError, ValueError):
                    continue
    path = Path(ctx.LOGS)/f'result_attempts_{hd}.json'
    state = _load(path, hd)
    # A yesterday HD queried after midnight still has yesterday's deadline.
    date = datetime.datetime.strptime(hd, '%Y%m%d').replace(tzinfo=now.tzinfo)
    never = date-datetime.timedelta(days=1)
    todo = []
    notices = notices.drop_duplicates(['jcd', 'rno'], keep='last')
    for row in notices.to_dict('records'):
        try:
            key = (int(row['jcd']), int(row['rno']))
            if key in got:
                continue
            hour, minute = map(int, str(row['deadline']).split(':'))
            deadline = date.replace(hour=hour, minute=minute)
            if (now-deadline).total_seconds() < ctx.RESULT_WAIT_MIN*60:
                continue
            name = f'{key[0]}-{key[1]}'
            metrics['pending'] += 1
            attempt = state['races'].get(name, {})
            due = _stamp(attempt.get('next_attempt_at'), never)
            if due > now:
                metrics['backoff'] += 1
                continue
            last = _stamp(attempt.get('last_attempt_at'), never)
            todo.append((last, deadline, key, row, name, attempt))
        except (KeyError, TypeError, ValueError):
            continue
    # Never-tried new races outrank old failed ones, regardless of deadline.
    todo.sort(key=lambda item: (item[0], item[1], item[2]))
    metrics['due'] = len(todo)
    for _, _, (jcd, rno), notice, name, previous in todo[:ctx.RESULT_MAX]:
        metrics['attempted'] += 1
        count = max(0, int(previous.get('attempts', 0)))+1
        wait = min(BACKOFF_MAX_MIN, BACKOFF_MIN*2**min(count-1, 8))
        attempt = dict(attempts=count, last_attempt_at=now.isoformat(),
                       next_attempt_at=(now+datetime.timedelta(minutes=wait)).isoformat(),
                       status='attempting')
        state['races'][name] = attempt
        _save(path, state, now)  # Persist reservation even if the runner dies.
        html, result = '', None
        try:
            html = ctx.page('raceresult', jcd, rno, hd)
            result = ctx.parse_result_details(html)
        except Exception as exc:
            attempt['error'] = type(exc).__name__
        if not result:
            # A stale/empty relay response is not proof of unpublished results.
            try:
                response = ctx.get_direct(f'{ctx.BR}raceresult?rno={rno}&jcd={jcd:02d}&hd={hd}',
                                          timeout=20, tries=1)
                if response is not None and response.status_code == 200:
                    response.encoding = 'utf-8'
                    html = response.text
                    result = ctx.parse_result_details(html)
                    attempt['direct_fallback'] = True
            except Exception as exc:
                attempt['error'] = type(exc).__name__
        if html:
            metrics['fetched'] += 1
        if isinstance(result, dict) and result:
            # Preserve parser-returned status/payouts; never fill unknowns by 0.
            ctx.save_sample(hd, 'raceresult', html)
            ctx.append_rows(ctx.result_path(hd), ctx.RESULT_COLS,
                            [dict(hd=hd, jcd=jcd, venue=notice.get('venue', ''), rno=rno,
                                  deadline=notice['deadline'], **result,
                                  got_at=f'{ctx.now_jst():%H:%M}')])
            metrics['saved'] += 1
            is_settled = confirmed(result)
            metrics['settled'] += int(is_settled)
            metrics['pending'] -= int(is_settled)
            attempt['status'] = 'settled' if is_settled else 'pending'
        else:
            attempt['status'] = 'pending'
            if not html:
                attempt.setdefault('error', 'NoOfficialResponse')
            metrics['errors'] += int('error' in attempt)
        _save(path, state, now)
    if metrics['saved']:
        print('結果を記録', metrics['saved'], 'レース')
    return metrics
