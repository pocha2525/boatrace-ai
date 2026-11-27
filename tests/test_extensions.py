import base64
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import cm
import common
import delivery
import evaluation
import predict
import settlement
import train
import venue_data
from ot.parse import parse_page


@pytest.mark.parametrize('venue,lap,turn,straight', [
    ('gamagori', 36.81, 4.99, 6.36),
    ('omura', 37.40, 6.53, 7.40),
    ('marugame', 37.43, 5.30, 6.00),
    ('miyajima', 37.74, 5.42, 7.20),
])
def test_actual_official_numeric_tables(venue, lap, turn, straight):
    html = json.loads((Path(__file__).parent/'official_tables.json').read_text())[venue]
    rows, _ = parse_page(html)
    assert [r['lane'] for r in rows] == list(range(1, 7))
    assert (rows[0]['lap'], rows[0]['turn'], rows[0]['straight']) == (lap, turn, straight)


def test_motor_rating_table_is_not_an_original_time():
    html = '<table><tr><th>枠</th><th>回り足</th></tr><tr><td>1</td><td>強い</td></tr></table>'
    assert parse_page(html)[0] == []


def test_static_page_cannot_be_used_for_past_date():
    venue_data.clear()
    def forbidden(*a, **k):
        raise AssertionError('must not request current-only page for an old day')
    assert venue_data.html(forbidden, 15, 6, '20200101') == ''


@pytest.mark.parametrize('text,sign', [
    ('良くなった', 1), ('良くなっている', 1), ('悪くない', 1),
    ('下がることはない', 1), ('不安はない', 1), ('悪い', -1),
    ('良くない', -1), ('合っていない', -1), ('出ていない', -1),
    ('良くしたい', 0), ('よくしたい', 0),
])
def test_comment_polarity_regressions(text, sign):
    p, n = cm.score(text)
    assert np.sign(p-n) == sign


def test_gamagori_js_is_parsed_without_execution():
    text = "function funcToDayComment(arg){ if(strTouban === '5174'){ strComment = '良い'; } return strComment; }"
    assert cm.parse_gamagori_js(text) == {5174: '良い'}


def test_comment_table_handles_adjacent_spans_and_header_td():
    html = '<table><tr><td>枠</td><td>選手名/登番</td><td>選手コメント</td></tr><tr><td>1</td><td><span>A2</span><span>5009</span>名前</td><td>良い</td></tr></table>'
    assert cm.parse_table(html) == {1: (5009, '良い')}


def test_refund_does_not_make_unrelated_losing_ticket_a_refund():
    result = dict(tri='1-2-3', tri_pay=1500, refund_boats='6')
    assert settlement.settle('1-2-6', result) == ('refunded', 0, 100, '返還')
    assert settlement.settle('1-3-2', result)[:3] == ('settled', 0, 0)
    assert settlement.settle('1-2-3', result)[:3] == ('settled', 1, 1500)


def test_dead_heat_and_missing_results_are_distinct():
    r = dict(tri_payouts={'1-2-3': 500, '1-3-2': 700})
    assert settlement.settle('1-3-2', r)[:3] == ('settled', 1, 700)
    assert settlement.settle('1-2-3', {})[:3] == ('pending', None, None)
    assert settlement.settle('1-2-3', dict(race_status='cancelled'))[:3] == ('refunded', 0, 100)


def test_ledger_fails_closed_without_actions_token(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    assert delivery.Ledger().reserve('race-1') is None


def test_existing_pending_reservation_is_not_retried(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_TOKEN', 'test-only')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    class Response:
        status_code = 200
        def json(self):
            return {'sha': 'test', 'content': base64.b64encode(b'{"status":"pending"}').decode()}
    monkeypatch.setattr(delivery.requests, 'get', lambda *a, **k: Response())
    def forbidden(*a, **k):
        raise AssertionError('must not replace an existing uncertain reservation')
    monkeypatch.setattr(delivery.requests, 'put', forbidden)
    assert delivery.Ledger().reserve('race-1') is None


def test_crash_after_delivery_restores_bets_once(monkeypatch, tmp_path):
    monkeypatch.setattr(predict, 'LOGS', str(tmp_path))
    folder = tmp_path/'delivery'; folder.mkdir()
    notice = dict(notify_id='20261127-01-01', hd='20261127', jcd=1, rno=1, venue='桐生')
    bets = [dict(hd='20261127', jcd=1, rno=1, pick='1-2-3', prob=.15)]
    (folder/'a.json').write_text(json.dumps(dict(status=common.SENT_OK, receipt=dict(notify=notice, bets=bets))))
    predict.restore_deliveries('20261127')
    predict.restore_deliveries('20261127')
    assert len(pd.read_csv(tmp_path/'20261127.csv')) == 1


def test_actual_course_history_is_queried_using_exhibition_course():
    history = pd.DataFrame(dict(date=[20261120]*6+[20261121], racer=[1234]*7,
                                course=[2]*7, win=[1, 0, 1, 0, 1, 0, 1]))
    q = pd.DataFrame(dict(date=[20261120, 20261121, 20261122], racer=[1234]*3, course=[2]*3))
    values = train.past_lookup(history, q, ['racer', 'course'], 'win', 'rc_win')
    assert np.isnan(values[0])
    assert values[1] == .5  # Excludes today's winner.
    assert values[2] == 4/7


def test_evaluation_dates_are_consumed_even_when_challenger_loses(tmp_path):
    p = tmp_path/'evaluation.json'
    frame = pd.DataFrame(dict(date=[20261120, 20261121, 20261122]))
    evaluation.consume(p, 20261121)
    assert evaluation.fresh_rows(frame, p, 20261120).date.tolist() == [20261122]


def test_promotion_requires_evidence_across_days():
    days = np.repeat(np.arange(7), 100)
    reference = np.zeros(len(days))
    assert evaluation.compare(reference, reference+.01, days)['approved']
    assert not evaluation.compare(reference, reference-.01, days)['approved']
    assert not evaluation.compare(reference[:100], reference[:100]+.01, days[:100])['approved']


def test_training_uses_exhibition_course_and_weather_not_final_race(monkeypatch):
    import nightly
    import before
    rows, races = [], []
    for day in range(1, 9):
        date = 20261100 + day
        races.append(dict(date=date, venue=11, race=1, wind=10., wave=30., wind_dir='北'))
        for boat in range(1, 7):
            rows.append(dict(date=date, venue=11, race=1, racer=4000+boat,
                             boat=boat, course=boat, motor=boat, tenji=6.8,
                             st=.15, rank=boat, rank_raw=str(boat)))
    history = nightly.prepare(pd.DataFrame(rows), pd.DataFrame(races))
    observed = []
    for boat in range(1, 7):
        row = {c: np.nan for c in before.COLS}
        row.update(hd=20261108, jcd=11, rno=1, boat=boat, racer=4000+boat,
                   ex_course={2: 3, 3: 2}.get(boat, boat), ex_st=.1,
                   tenji=6.7, wind=3., wave=5., wind_img=1)
        observed.append(row)
    monkeypatch.setattr(before, 'load_hist', lambda path: pd.DataFrame(observed)
                        if path != 'logs' else pd.DataFrame(columns=before.COLS))
    def add_columns(names):
        def f(df, *a, **k):
            for name in names:
                df[name] = np.nan
            return df
        return f
    monkeypatch.setattr(nightly.bfile, 'add_hist', add_columns(nightly.bfile.BFEATS))
    monkeypatch.setattr(nightly.ot, 'add_hist', add_columns(nightly.ot.OT))
    monkeypatch.setattr(nightly.cm, 'add_hist', add_columns(nightly.cm.CM))
    monkeypatch.setattr(nightly.bfile, 'img_dir', lambda *a: None)
    result = nightly.add_all(history, pd.DataFrame(races))
    last = result[result.date == 20261108]
    assert last['_observed'].all()
    boat2 = last[last.boat == 2].iloc[0]
    assert boat2.course == 3
    assert np.isnan(boat2.rc_win)  # No prior actual-course-3 history for this racer.
    assert boat2.wind == 3 and boat2.wave == 5 and boat2.tenji == 6.7
    assert result.loc[result.date < 20261108, 'wind'].isna().all()


def test_reservation_compare_and_swap_conflict_blocks_delivery(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_TOKEN', 'test-only')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    class Response:
        def __init__(self, code): self.status_code = code
    monkeypatch.setattr(delivery.requests, 'get', lambda *a, **k: Response(404))
    monkeypatch.setattr(delivery.requests, 'put', lambda *a, **k: Response(409))
    assert delivery.Ledger().reserve('race-1') is None


def test_ledger_failure_never_sends_to_ntfy(monkeypatch):
    monkeypatch.setenv('NTFY_TOPIC', 'test-only')
    monkeypatch.setattr(delivery.Ledger, 'reserve', lambda *a: None)
    def forbidden(*a, **k): raise AssertionError('notification must not be sent')
    monkeypatch.setattr(common, '_send_notify', forbidden)
    assert common.send_notify('買い', '通知ID: 20261127-11-05')['status'] == common.SENT_UNKNOWN


def test_result_parser_reads_multiple_winners_and_refund_boats():
    def row(boats, pay, label=''):
        return '<tr><td>'+label+'</td><td>'+''.join(
            f'<span class="numberSet1_number">{b}</span>' for b in boats)+f'</td><td>￥{pay}</td></tr>'
    html = '<table><tbody>'+row('123', 500, '3連単')+row('132', 700)+'</tbody></table>'
    html += '<table><thead><tr><th>返還</th></tr></thead><tbody><tr><td><span class="numberSet1_number">6</span></td></tr></tbody></table>'
    result = predict.parse_result_details(html)
    assert json.loads(result['tri_payouts']) == {'1-2-3': 500, '1-3-2': 700}
    assert result['refund_boats'] == '6'


def test_result_csv_schema_upgrade_keeps_previous_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(predict, 'LOGS', str(tmp_path))
    path = tmp_path/'results.csv'
    path.write_text('hd,tri\n20261126,1-2-3\n')
    predict.append_rows(str(path), ['hd', 'tri', 'refund_boats'],
                        [dict(hd='20261127', tri='1-3-2', refund_boats='6')])
    d = pd.read_csv(path, dtype=str)
    assert d.tri.tolist() == ['1-2-3', '1-3-2']
    assert pd.isna(d.iloc[0].refund_boats) and d.iloc[1].refund_boats == '6'


def test_late_comment_does_not_replace_a_verified_capture(monkeypatch):
    row = dict(hd=20261127, jcd=11, rno=5, boat=1, racer=5009, text='良い',
               cm_pos=1., cm_neg=0., cm_score=1., cm_mot=np.nan, asof_verified=1)
    monkeypatch.setattr(cm, 'load_hist', lambda roots: pd.DataFrame(
        [row, dict(row, text='悪い', asof_verified=0)]))
    query = pd.DataFrame([dict(date=20261127, venue=11, race=5, boat=1, racer=5009)])
    result = cm.add_hist(query, [])
    assert result.cm_score.iloc[0] == 1
    query['racer'] = 5008
    assert cm.add_hist(query, []).cm_score.isna().all()


def test_unverified_legacy_comments_are_excluded_from_training(monkeypatch):
    row = dict(hd=20261127, jcd=11, rno=5, boat=1, racer=5009, text='良い',
               cm_pos=1., cm_neg=0., cm_score=1., cm_mot=np.nan)
    monkeypatch.setattr(cm, 'load_hist', lambda roots: pd.DataFrame([row]))
    query = pd.DataFrame([dict(date=20261127, venue=11, race=5, boat=1, racer=5009)])
    assert cm.add_hist(query, []).cm_score.isna().all()


def test_comment_capture_is_verified_only_before_deadline(monkeypatch, tmp_path):
    now = common.now_jst()
    monkeypatch.setattr(common, 'now_jst', lambda: now)
    rows = {1: dict(reg=5009, text='良い', cm_pos=1, cm_neg=0, cm_score=1, cm_mot=np.nan)}
    cm.append(str(tmp_path), '20261127', 11, 5, rows, deadline=now+common.datetime.timedelta(minutes=1))
    cm.append(str(tmp_path), '20261127', 11, 6, rows, deadline=now-common.datetime.timedelta(minutes=1))
    stored = pd.read_csv(tmp_path/'comment_20261127.csv')
    assert stored.asof_verified.tolist() == [1, 0]
