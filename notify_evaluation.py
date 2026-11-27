"""Notify completed diagnostic experiments; never claim a model was promoted."""
import glob
import json
import os
from ticket_comparison import enrich


def verified_conclusion(run_id, repository, conclusion):
    if conclusion != 'unknown':
        return conclusion
    import requests
    if not str(run_id).isdigit():
        raise ValueError('Invalid workflow run ID')
    try:
        response = requests.get(f'https://api.github.com/repos/{repository}/actions/runs/{run_id}',
                                headers={'Authorization': 'Bearer '+os.environ.get('GITHUB_TOKEN', '')},
                                timeout=20)
        response.raise_for_status()
        run = response.json()
        if run.get('status') == 'completed':
            return run.get('conclusion') or 'unknown'
    except (requests.RequestException, ValueError):
        pass
    return 'unknown'


def message(summaries, run_id, repository, conclusion='success'):
    lines = []
    for d in sorted(summaries, key=lambda s: s['holdout_from']):
        mode = '展示照合済' if d.get('input_mode', 'observed') == 'observed' else '枠なり仮定'
        lines.append(f"{d['holdout_from']}〜{d['holdout_until']}（{mode}）")
        for name, label in [('baseline_66_refit', '基準'), ('candidate', '追加'), ('candidate_recent', '直近成績')]:
            if name not in d['metrics']:
                continue
            v = d['metrics'][name]
            if 'selected12_n' in v:
                n = v['selected12_n']
                if n:
                    lines.append(f"{label}{v['features']}項目: 確率12%以上{n}R／的中{v['selected12_hit_pct']:.1f}%／固定1点回収{v['selected12_roi_pct']:.1f}%")
                    if v.get('selected12_two_roi_pct') is not None:
                        lines.append(f"  固定2点: 的中{v['selected12_two_hit_pct']:.1f}%／回収{v['selected12_two_roi_pct']:.1f}%／収支{v['two_profit']:+,.0f}円（各100円）")
                else:
                    lines.append(f"{label}: 確率12%以上の対象なし")
            else:
                lines.append(f"{label}: 全{v['races']}R／本命的中{v['top1_hit_pct']:.1f}%／固定1点回収{v['fixed1_roi_pct']:.1f}%")
    if not summaries:
        lines.append('結果を確定できませんでした。実行ログを確認します。')
    elif conclusion in {'failure', 'timed_out', 'cancelled', 'action_required'}:
        lines.append('一部の実行が未完了です。上記は保存できた分のみです。')
    elif conclusion != 'success':
        lines.append('保存済みの結果を集計しています。実行の完了状態はこの通知では未照合です。')
    lines += ['※本命確率12%以上の同じ対象で、上位1点と上位2点を各100円購入した比較。探索用の過去検証で本番の期待値条件は未再現。少数例の100%超で採用せず、本番モデルは変更していません。',
              'https://github.com/'+repository+'/actions/runs/'+str(run_id)]
    return '\n'.join(lines)


def main():
    from common import send_notify, SENT_OK
    run_id = os.environ['BACKTEST_RUN_ID']
    paths = glob.glob('logs/backtest/*_'+run_id+'/summary.json')+glob.glob('logs/refine/*_'+run_id+'/summary.json')
    summaries = [enrich(json.load(open(p, encoding='utf8')), p) for p in paths]
    conclusion = verified_conclusion(run_id, os.environ['GITHUB_REPOSITORY'], os.environ.get('BACKTEST_CONCLUSION', 'unknown'))
    text = message(summaries, run_id, os.environ['GITHUB_REPOSITORY'], conclusion)
    result = send_notify('1点・2点の検証結果' if summaries else '検証結果を確定できず', text,
                         notification_id='backtest-result-'+run_id+'-tickets-v3')
    print('通知受付:', result['status'])
    if result['status'] != SENT_OK:
        raise SystemExit('通知の受付を確認できませんでした')


if __name__ == '__main__':
    main()
