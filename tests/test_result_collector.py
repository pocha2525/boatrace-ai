import csv
import datetime
import json
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

import common
import result_collector

NOW = datetime.datetime.fromisoformat('2026-11-28T12:00:00+09:00')
PAY = dict(tri='1-2-3', tri_pay=720, tri_payouts='{"1-2-3": 720}', refund_boats='', race_status='settled')


def context(tmp_path, races=1, result=None):
    notices = pd.DataFrame([dict(hd='20261128', jcd=18, rno=n, venue='徳山', deadline=f'10:{n:02d}',
                                notify_id=f'20261128-18-{n:02d}') for n in range(1, races+1)])
    notices.to_csv(tmp_path/'notify_20261128.csv', index=False)
    if result:
        pd.DataFrame([dict(hd='20261128', jcd=18, rno=1, **result)]).to_csv(tmp_path/'results_20261128.csv', index=False)
    calls, direct, writes = [], [], []
    def read_log(path):
        return pd.read_csv(path, dtype=str).fillna('') if Path(path).exists() else None
    def get(url, **kwargs):
        query = parse_qs(urlparse(url).query)
        calls.append(('raceresult', int(query['jcd'][0]), int(query['rno'][0]), query['hd'][0]))
        assert kwargs == dict(tries=1, timeout=25, direct_fallback=False, relay_only=True)
        return None
    def get_direct(url, **kwargs):
        direct.append((url, kwargs))
        return None
    def append(path, columns, rows):
        writes.extend(rows)
    ctx = SimpleNamespace(LOGS=str(tmp_path), BR='https://official.test/', RESULT_MAX=2, RESULT_WAIT_MIN=12,
                          RESULT_COLS=[], read_log=read_log, notify_path=lambda hd: str(tmp_path/f'notify_{hd}.csv'),
                          result_path=lambda hd: str(tmp_path/f'results_{hd}.csv'), get=get, get_direct=get_direct,
                          parse_result_details=lambda text: dict(PAY) if text == 'settled' else None,
                          save_sample=lambda *a: None, append_rows=append, now_jst=lambda: NOW)
    return ctx, calls, direct, writes


def test_new_untried_races_outcome_collection_not_starved_by_old_failures(tmp_path):
    ctx, calls, direct, writes = context(tmp_path, races=5)
    first = result_collector.collect('20261128', NOW, ctx)
    assert first['attempted'] == 2 and first['saved'] == 0
    result_collector.collect('20261128', NOW+datetime.timedelta(minutes=10), ctx)
    assert [x[2] for x in calls] == [1, 2, 3, 4]
    assert not writes
    state = json.loads((tmp_path/'result_attempts_20261128.json').read_text())
    assert set(state['races']) == {'18-1', '18-2', '18-3', '18-4'}


def test_durable_exponential_backoff_and_bounded_direct_attempt(tmp_path):
    ctx, calls, direct, writes = context(tmp_path)
    first = result_collector.collect('20261128', NOW, ctx)
    assert first['errors'] == 1 and first['pending'] == 1
    metrics = result_collector.collect('20261128', NOW+datetime.timedelta(minutes=4), ctx)
    assert metrics['attempted'] == 0 and metrics['backoff'] == 1
    result_collector.collect('20261128', NOW+datetime.timedelta(minutes=5), ctx)
    assert len(calls) == len(direct) == 2 and not writes
    assert all(x[1] == dict(timeout=8, tries=1) for x in direct)
    state = json.loads((tmp_path/'result_attempts_20261128.json').read_text())['races']['18-1']
    assert state['attempts'] == 2
    assert state['next_attempt_at'] == (NOW+datetime.timedelta(minutes=15)).isoformat()


def test_hd_date_is_used_when_collecting_after_midnight(tmp_path):
    ctx, calls, direct, writes = context(tmp_path)
    path = tmp_path/'notify_20261128.csv'
    table = pd.read_csv(path)
    table['deadline'] = '23:30'; table.to_csv(path, index=False)
    next_day = datetime.datetime.fromisoformat('2026-11-29T00:05:00+09:00')
    assert result_collector.collect('20261128', next_day, ctx)['attempted'] == 1
    assert calls[0][3] == '20261128'


def test_confirmed_payout_is_not_refetched_or_rewritten(tmp_path):
    ctx, calls, direct, writes = context(tmp_path, result=PAY)
    result_file = tmp_path/'results_20261128.csv'
    before = result_file.read_bytes()
    assert result_collector.collect('20261128', NOW, ctx)['attempted'] == 0
    assert not calls and not direct and not writes
    assert result_file.read_bytes() == before


@pytest.mark.parametrize('result', [dict(PAY, race_status='pending'), dict(PAY, race_status='cancelled'),
                                    dict(tri='返還', tri_pay='', tri_payouts='{}', refund_boats='6', race_status='pending')])
def test_non_final_rows_still_eligible_for_collection(tmp_path, result):
    ctx, calls, direct, writes = context(tmp_path, result=result)
    assert result_collector.collect('20261128', NOW, ctx)['attempted'] == 1
    assert not writes  # no fabricated zero or substituted payout


def test_one_direct_success_fallback_keeps_exact_parsed_official_values(tmp_path):
    ctx, calls, direct, writes = context(tmp_path)
    def fetch(url, **kwargs):
        direct.append((url, kwargs))
        return SimpleNamespace(status_code=200, text='settled', encoding=None)
    ctx.get_direct = fetch
    metrics = result_collector.collect('20261128', NOW, ctx)
    assert metrics['settled'] == metrics['saved'] == metrics['attempted'] == 1
    assert metrics['pending'] == metrics['errors'] == 0
    assert len(direct) == 1
    assert all(writes[0][k] == v for k, v in PAY.items())


def test_non_200_direct_response_does_not_parse_or_settle(tmp_path):
    ctx, calls, direct, writes = context(tmp_path)
    ctx.get_direct = lambda *a, **k: SimpleNamespace(status_code=503, text='settled')
    assert result_collector.collect('20261128', NOW, ctx)['saved'] == 0
    assert not writes


def test_normal_official_result_does_not_make_fallback_request(tmp_path):
    ctx, calls, direct, writes = context(tmp_path)
    ctx.get = lambda *a, **k: SimpleNamespace(status_code=200, text='settled', encoding=None)
    assert result_collector.collect('20261128', NOW, ctx)['settled'] == 1
    assert not direct and len(writes) == 1


def test_relay_5xx_tries_then_one_direct_success(monkeypatch):
    monkeypatch.setattr(common, 'RELAY', 'https://relay.test/')
    monkeypatch.setattr(common.time, 'sleep', lambda _: None)
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=503 if url == common.RELAY else 200)
    monkeypatch.setattr(common.requests, 'get', get)
    assert common.get('https://official.test/', tries=2).status_code == 200
    assert [x[0] for x in calls] == [common.RELAY, common.RELAY, 'https://official.test/']
    assert calls[-1][1]['timeout'] == 20


def test_relay_timeout_has_one_direct_fallback(monkeypatch):
    monkeypatch.setattr(common, 'RELAY', 'https://relay.test/')
    monkeypatch.setattr(common.time, 'sleep', lambda _: None)
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if url == common.RELAY:
            raise requests.Timeout('mock-only')
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(common.requests, 'get', get)
    assert common.get('https://official.test/', tries=1).status_code == 200
    assert calls == [common.RELAY, 'https://official.test/']


def test_valid_relay_404_does_not_fallback(monkeypatch):
    monkeypatch.setattr(common, 'RELAY', 'https://relay.test/')
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {'status': 404})
    monkeypatch.setattr(common.requests, 'get', get)
    assert common.get('https://official.test/') is None
    assert calls == [common.RELAY]


def test_relay_permission_denial_does_not_trigger_transport_fallback(monkeypatch):
    monkeypatch.setattr(common, 'RELAY', 'https://relay.test/')
    monkeypatch.setattr(common.time, 'sleep', lambda _: None)
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=403)
    monkeypatch.setattr(common.requests, 'get', get)
    assert common.get('https://official.test/', tries=1) is None
    assert calls == [common.RELAY]


def test_monotonic_budget_yields_queue_without_marking_unattempted(tmp_path, monkeypatch):
    ctx, calls, direct, writes = context(tmp_path, races=8)
    ctx.RESULT_MAX = 8
    clock = [0.0]
    monkeypatch.setattr(result_collector.time, 'monotonic', lambda: clock[0])
    def relay(url, **kwargs):
        rno = int(parse_qs(urlparse(url).query)['rno'][0])
        calls.append(rno)
        clock[0] += kwargs['timeout']
        return None
    def official(url, **kwargs):
        direct.append(kwargs)
        clock[0] += kwargs['timeout']
        return None
    ctx.get, ctx.get_direct = relay, official
    metrics = result_collector.collect('20261128', NOW, ctx)
    assert clock[0] == 90
    assert metrics['budget_exhausted'] == 1
    assert metrics['attempted'] == 3 and metrics['due'] == 8
    assert calls == [1, 2, 3]
    assert len(direct) == 2
    assert all(attempt == dict(timeout=8, tries=1) for attempt in direct)
    state = json.loads((tmp_path/'result_attempts_20261128.json').read_text())
    assert set(state['races']) == {f'18-{n}' for n in range(1, 4)}
    # The next tick starts with untouched races, ahead of old failures.
    clock[0] = 0
    result_collector.collect('20261128', NOW+datetime.timedelta(minutes=5), ctx)
    assert calls[3:5] == [4, 5]
    assert not writes


def test_budget_exhausted_in_relay_skips_direct_fallback(tmp_path, monkeypatch):
    ctx, calls, direct, writes = context(tmp_path, races=2)
    clock = [0.0]
    monkeypatch.setattr(result_collector.time, 'monotonic', lambda: clock[0])
    def relay(url, **kwargs):
        assert kwargs['timeout'] == 25
        clock[0] = 90
        return None
    ctx.get = relay
    metrics = result_collector.collect('20261128', NOW, ctx)
    assert metrics['attempted'] == 1 and metrics['budget_exhausted'] == 1
    assert not direct and not writes
    state = json.loads((tmp_path/'result_attempts_20261128.json').read_text())
    assert set(state['races']) == {'18-1'}


def test_result_max_is_always_capped_at_eight(tmp_path):
    ctx, calls, direct, writes = context(tmp_path, races=12)
    ctx.RESULT_MAX = 99
    assert result_collector.collect('20261128', NOW, ctx)['attempted'] == 8
    assert len(calls) == len(direct) == 8


def test_result_relay_failure_has_exactly_one_direct_request(tmp_path, monkeypatch):
    ctx, calls, direct, writes = context(tmp_path)
    ctx.BR = 'https://official.test/'
    ctx.get, ctx.get_direct = common.get, common.get_direct
    monkeypatch.setattr(common, 'RELAY', 'https://relay.test/')
    monkeypatch.setattr(common.time, 'sleep', lambda _: None)
    transport = []
    def relay(url, **kwargs):
        transport.append((url, kwargs))
        raise requests.Timeout('mock-only')
    def official(method, url, **kwargs):
        transport.append((url, kwargs))
        return SimpleNamespace(status_code=200, text='settled', encoding=None)
    monkeypatch.setattr(common.requests, 'get', relay)
    monkeypatch.setattr(common.requests, 'request', official)
    metrics = result_collector.collect('20261128', NOW, ctx)
    assert metrics['settled'] == 1
    assert len(transport) == 2
    assert transport[0][0] == common.RELAY
    assert transport[1][0].startswith(ctx.BR)
    assert [kwargs['timeout'] for _, kwargs in transport] == [25, 8]
    assert writes[0]['tri_pay'] == 720


def test_empty_successful_relay_uses_one_official_direct_request(tmp_path, monkeypatch):
    ctx, calls, direct, writes = context(tmp_path)
    ctx.get, ctx.get_direct = common.get, common.get_direct
    monkeypatch.setattr(common, 'RELAY', 'https://relay.test/')
    transport = []
    def relay(url, **kwargs):
        transport.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {'status': 200, 'body': ''})
    def official(method, url, **kwargs):
        transport.append(url)
        return SimpleNamespace(status_code=200, text='settled', encoding=None)
    monkeypatch.setattr(common.requests, 'get', relay)
    monkeypatch.setattr(common.requests, 'request', official)
    assert result_collector.collect('20261128', NOW, ctx)['settled'] == 1
    assert transport == [common.RELAY, 'https://official.test/raceresult?rno=1&jcd=18&hd=20261128']
    assert writes[0]['tri_payouts'] == PAY['tri_payouts']


def test_no_relay_configuration_makes_only_one_direct_request(tmp_path, monkeypatch):
    ctx, calls, direct, writes = context(tmp_path)
    ctx.get, ctx.get_direct = common.get, common.get_direct
    monkeypatch.setattr(common, 'RELAY', '')
    monkeypatch.setattr(common.requests, 'get', lambda *a, **k: pytest.fail('relay-only must not make a direct request'))
    transport = []
    def official(method, url, **kwargs):
        transport.append(url)
        return SimpleNamespace(status_code=200, text='settled', encoding=None)
    monkeypatch.setattr(common.requests, 'request', official)
    assert result_collector.collect('20261128', NOW, ctx)['settled'] == 1
    assert len(transport) == 1


def test_context_without_get_uses_one_bounded_direct_request(tmp_path):
    ctx, calls, direct, writes = context(tmp_path)
    del ctx.get
    result_collector.collect('20261128', NOW, ctx)
    assert not calls
    assert len(direct) == 1
    assert direct[0][1] == dict(timeout=8, tries=1)
