"""夜間の自動改善（毎朝5時台に1回。GASが起動する）

1. 毎日：前日までの全成績で、選手・モーターなどの成績表（model/cur_*.csv）と、
   今節（同じ会場で続けて走る節）の成績表（cur_setsu*.csv）を作り直す。
2. 月曜の朝だけ（手動実行のときも）：今の材料27個に今節の材料6個を足した新しいモデル
   （挑戦者）を作り、直近28日の結果で今のモデルと比べる。はっきり良いときだけ入れ替える。
   入れ替える前のモデルは model/archive/日付/ に残すので、いつでも戻せる。
結果は通知と logs/nightly_log.csv に残す。
"""
import os
import shutil
import datetime

import numpy as np
import pandas as pd
import lightgbm as lgb

from common import MODEL, FEATS, X, TARGETS, now_jst, notify, add_rel, k_path
import train
import setsu
import extra
import bfile
import before
import backfill
import ot
import cm

EVAL_DAYS = 28       # 比べる期間（どちらのモデルも学習に使っていない直近の日数）
MIN_GAIN = 0.002     # 3連単の当てはまり（1レース平均の対数）がこれ以上良くなったら入れ替え
LOG = os.path.join('logs', 'nightly_log.csv')


def prepare(df, rc):
    """train.py と同じ前処理"""
    df = df.dropna(subset=['course']).copy()
    df['course'] = df['course'].astype(int)
    df['year'] = df['date'] // 10000
    df['win'] = (df['rank'] == 1).astype(int)
    df['top2'] = (df['rank'] <= 2).astype(int)
    df['top3'] = (df['rank'] <= 3).astype(int)
    df['st_ok'] = df['st'].where(df['st'] < 1)
    return df


def write_cur(df):
    """予想で使う成績表（全期間）を作り直す。train.py と同じ計算"""
    for keys, col, name in FEATS:
        g = df.groupby(keys)[col].agg(['sum', 'count'])
        g[name] = g['sum'] / g['count'].where(g['count'] >= 5)
        g[[name]].reset_index().to_csv(
            os.path.join(MODEL, f'cur_{name}.csv'), index=False)


def features(df, rc):
    """学習・比較用：その日より前の成績だけで特徴量を作る（train.py と同じ）"""
    for keys, col, name in FEATS:
        df = train.past(df, keys, col, name)
    keys = ['date', 'venue', 'race']
    df = df.merge(rc[keys + ['wind', 'wave']], on=keys, how='left')
    df = add_rel(df, keys)
    one = df[df['course'] == 1][keys + ['rc_win_d']]
    one = one.drop_duplicates(keys).rename(columns={'rc_win_d': 'in_d'})
    return df.merge(one, on=keys, how='left')


def trifecta_score(models, ev, temperature=1.0, details=False):
    """3連単の当てはまり（実際の着順の確率の対数の平均）と、本命の的中率"""
    keys = ['date', 'venue', 'race']
    ev = ev.sort_values(keys + ['boat'])
    n6 = ev.groupby(keys)['boat'].transform('count')
    ev = ev[n6 == 6]
    w, t2, t3 = [m.predict(ev[m.feature_name()]).reshape(-1, 6) for m in models]
    w = w / w.sum(1, keepdims=True)
    t2 = t2 * 2 / t2.sum(1, keepdims=True)
    t3 = t3 * 3 / t3.sum(1, keepdims=True)
    q2 = np.clip(t2 - w, 1e-3, None)
    q3 = np.clip(t3 - t2, 1e-3, None)
    rank = ev['rank'].values.reshape(-1, 6)
    lls, hits, dates = [], [], []
    for i in range(len(w)):
        r = rank[i]
        try:
            a, b, c = [int(np.where(r == k)[0][0]) for k in (1, 2, 3)]
        except IndexError:
            continue  # 失格・欠場などで1〜3着がそろわない
        p = {}
        for x in range(6):
            for y in range(6):
                for z in range(6):
                    if len({x, y, z}) == 3:
                        p[(x, y, z)] = (w[i, x] * q2[i, y] / (q2[i].sum() - q2[i, x])
                                        * q3[i, z] / (q3[i].sum() - q3[i, x] - q3[i, y]))
        values = np.array(list(p.values())) ** (1.0 / temperature)
        values /= values.sum()
        p = dict(zip(p, values))
        dates.append(int(ev['date'].values.reshape(-1, 6)[i, 0]))
        lls.append(np.log(max(p[(a, b, c)], 1e-9)))
        hits.append(max(p, key=p.get) == (a, b, c))
    if details:
        return lls, hits, dates
    return float(np.mean(lls)), float(np.mean(hits)) * 100, len(lls)


def download_b_all():
    """番組表ファイル（過去分）をそろえる"""
    from concurrent.futures import ThreadPoolExecutor
    end = now_jst().date()
    days, d = [], train.START
    while d <= end:
        if not os.path.exists(bfile.b_path(d)):
            days.append(d)
        d += datetime.timedelta(days=1)
    print('番組表 未取得', len(days))
    with ThreadPoolExecutor(4) as ex:
        for i, _ in enumerate(ex.map(bfile.download_b, days)):
            if i % 100 == 0:
                print('番組表', i, '/', len(days))


def add_all(df, rc):
    """学習・比較用の材料を全部そろえる"""
    df = extra.add_hist(setsu.add_hist(features(df, rc)))
    days = sorted({datetime.date(x // 10000, x // 100 % 100, x % 100)
                   for x in df['date'].unique()})
    df = bfile.add_hist(df, rc, days)
    df = ot.add_hist(df, os.environ.get('OT_ROOT', 'oriten'))   # オリジナル展示の過去分
    df = cm.add_hist(df, [backfill.CM_ROOT, 'logs'])   # 選手コメント
    historical = df.copy()
    parts = [part for part in [before.load_hist(backfill.ROOT), before.load_hist('logs')] if len(part)]
    bh = pd.concat(parts, ignore_index=True) if parts else before.load_hist(backfill.ROOT)
    names = {'hd': 'date', 'jcd': 'venue', 'rno': 'race'}
    bh = bh.rename(columns=names)
    k = ['date', 'venue', 'race', 'boat']
    cols = ['bf_wt', 'bf_tilt', 'bf_prop', 'bf_parts', 'bf_adj', 'ex_st', 'ex_f', 'stab']
    if len(bh):
        bh = bh.drop_duplicates(k, keep='last').rename(columns={
            'racer': '_bf_racer', 'ex_course': '_ex_course',
            'tenji': '_ex_tenji', 'wind': '_ex_wind', 'wave': '_ex_wave', 'wind_img': '_wind_img'})
        keep = k + cols + ['_bf_racer', '_ex_course', '_ex_tenji', '_ex_wind', '_ex_wave', '_wind_img']
        for c in keep:
            if c not in bh:
                bh[c] = np.nan
        df = df.merge(bh[keep], on=k, how='left')
        mismatch = df['_bf_racer'].notna() & (df['_bf_racer'] != df['racer'])
        df.loc[mismatch, cols + ['_ex_course', '_ex_tenji', '_ex_wind', '_ex_wave']] = np.nan
    else:
        for c in cols + ['_ex_course', '_ex_tenji', '_ex_wind', '_ex_wave', '_wind_img']:
            df[c] = np.nan
    observed = df[['_ex_course', '_ex_tenji', '_ex_wind', '_ex_wave']].notna().all(axis=1)
    df['_observed'] = observed.groupby([df.date, df.venue, df.race]).transform('all')
    old_tenji = df.tenji.copy()
    df['course'] = pd.to_numeric(df['_ex_course'], errors='coerce').fillna(df['boat']).astype(int)
    df['tenji'] = df['_ex_tenji'].combine_first(df['tenji'])
    # Final-race weather is unavailable before the race. Unknown stays NaN.
    df['wind'], df['wave'] = df['_ex_wind'], df['_ex_wave']
    df[['wd_x', 'wd_y']] = np.nan
    known = df['_wind_img'].notna() & df['_ex_wind'].notna()
    if known.any():
        directions = [bfile.img_dir(v, i) for v, i in zip(df.loc[known, 'venue'], df.loc[known, '_wind_img'])]
        df.loc[known, ['wd_x', 'wd_y']] = [
            bfile.wind_xy(direction, speed) if direction is not None else (np.nan, np.nan)
            for direction, speed in zip(directions, df.loc[known, '_ex_wind'])]
    for keys, col, name in FEATS:
        if 'course' in keys:
            df[name] = train.past_lookup(historical, df, keys, col, name)
    df['ss_tenji_d'] += df['tenji'] - old_tenji
    trends = historical[['date', 'venue', 'course', 'vs_cw']].drop_duplicates(
        ['date', 'venue', 'course'])
    df = df.drop(columns=['vs_cw']).merge(trends, on=['date', 'venue', 'course'], how='left')
    df = add_rel(df, ['date', 'venue', 'race'])
    one = df[df.course == 1][['date', 'venue', 'race', 'rc_win_d']].drop_duplicates(
        ['date', 'venue', 'race']).rename(columns={'rc_win_d': 'in_d'})
    df = df.drop(columns=['in_d']).merge(one, on=['date', 'venue', 'race'], how='left')
    df = before.add_rel(df, ['date', 'venue', 'race'])
    return df


def build_wind_map(rc):
    """公式サイトの風向の画像番号と、成績ファイルの風向を突き合わせて対応表を作る"""
    import glob
    fs = glob.glob(os.path.join(backfill.ROOT, 'before_*.csv')) + \
        glob.glob(os.path.join('logs', 'before_*.csv'))
    if not fs:
        return 0
    b = pd.concat([pd.read_csv(f, usecols=['hd', 'jcd', 'rno', 'wind_img'])
                   for f in fs], ignore_index=True).dropna()
    b = b.drop_duplicates(['hd', 'jcd', 'rno'], keep='last')
    b.columns = ['date', 'venue', 'race', 'img']
    b['date'] = b['date'].astype(int)
    m = b.merge(rc[['date', 'venue', 'race', 'wind_dir', 'wind']],
                on=['date', 'venue', 'race'])
    m = m[(m['wind'] > 0) & m['wind_dir'].isin(bfile.DIRS)]
    # 画像の向きは場ごとに違う（場の図に合わせて描かれている）ので、場×番号で対応を取る
    out = []
    for (v, img), g in m.groupby(['venue', 'img']):
        vc = g['wind_dir'].value_counts()
        if vc.iloc[0] >= 10 and vc.iloc[0] / len(g) >= 0.9:
            out.append((int(v), int(img), vc.index[0], int(vc.iloc[0]), len(g)))
    pd.DataFrame(out, columns=['venue', 'img', 'dir', 'match', 'n']).to_csv(
        os.path.join(MODEL, 'wind_img_map.csv'), index=False)
    return len(out)


def challenge(df, today):
    """挑戦者モデルを作り、直近28日で今のモデルと比べる。良ければ入れ替える"""
    cut = int((today - datetime.timedelta(days=EVAL_DAYS)).strftime('%Y%m%d'))
    # 今の材料 ＋ 今節 ＋ フライング歴・当日の前走 ＋ 出走表の数字・風向
    X2 = X + setsu.SETSU + extra.EXTRA + bfile.BFEATS + ot.OT + cm.CM
    # 直前情報（展示ST・チルト・部品交換など）は、比べる期間の8割以上と
    # 学習に60日以上そろったときだけ入れる
    ev_cov = df.loc[df['date'] >= cut, 'ex_st'].notna().mean() if 'ex_st' in df else 0
    tr_days = df.loc[(df['date'] < cut) & df['ex_st'].notna(), 'date'].nunique() \
        if 'ex_st' in df else 0
    use_bf = ev_cov >= 0.8 and tr_days >= 60
    if use_bf:
        X2 = X2 + before.BEFORE
    import evaluation
    state_path = os.path.join(MODEL, 'evaluation_state.json')
    holdout_from = int((today - datetime.timedelta(days=14)).strftime('%Y%m%d'))
    raw_holdout = evaluation.fresh_rows(df, state_path, holdout_from)
    if not len(raw_holdout) or raw_holdout['_observed'].mean() < 0.8:
        raise ValueError('展示時点の進入・気象を照合できる新規評価データが80%以上必要です')
    holdout = raw_holdout[raw_holdout['_observed']].copy()
    if holdout['date'].nunique() < 5:
        raise ValueError('再利用しない新しい評価日が5日分必要です')
    tr = df[(df['date'] >= train.TRAIN_FROM) & (df['date'] < cut)]
    ev = df[df['date'] >= cut]
    cand_dir = os.path.join(MODEL, 'candidate')
    os.makedirs(cand_dir, exist_ok=True)
    cand = []
    # scikit-learn を使わない LightGBM 本体の学習（設定は train.py と同じ）
    params = dict(objective='binary', learning_rate=0.05, num_leaves=63,
                  bagging_fraction=0.8, bagging_freq=1, feature_fraction=0.8,
                  verbose=-1)
    for y in TARGETS:
        ds = lgb.Dataset(tr[X2], tr[y], categorical_feature=['venue'],
                         free_raw_data=False)
        m = lgb.train(params, ds, num_boost_round=400)
        path = os.path.join(cand_dir, f'model_{y}.txt')
        m.save_model(path)
        cand.append(lgb.Booster(model_file=path))
        print(y, '挑戦者 学習完了')
    champ = [lgb.Booster(model_file=os.path.join(MODEL, f'model_{y}.txt'))
             for y in TARGETS]
    # Refit the reference on the same past-only cutoff. A saved champion may
    # already have seen part of this evaluation window; do not compare in-sample.
    reference = []
    for y, old in zip(TARGETS, champ):
        ds = lgb.Dataset(tr[old.feature_name()], tr[y], categorical_feature=['venue'])
        reference.append(lgb.train(params, ds, num_boost_round=400))
    validation = ev[ev['date'] < holdout_from]
    temps = [0.85, 1.0, 1.15, 1.3, 1.6]
    temperature = max(temps, key=lambda t: trifecta_score(cand, validation, t)[0])
    # Compare comment features against an ablation on validation only. The
    # final holdout is touched once, after this choice and temperature fitting.
    cm_validation_ll = trifecta_score(cand, validation, temperature)[0]
    without_cm = [name for name in X2 if name not in cm.CM]
    ablation = []
    for y in TARGETS:
        ds = lgb.Dataset(tr[without_cm], tr[y], categorical_feature=['venue'])
        ablation.append(lgb.train(params, ds, num_boost_round=400))
    ablation_temperature = max(temps, key=lambda t: trifecta_score(ablation, validation, t)[0])
    no_cm_validation_ll = trifecta_score(ablation, validation, ablation_temperature)[0]
    use_cm = cm_validation_ll >= no_cm_validation_ll + MIN_GAIN
    if not use_cm:
        cand, X2, temperature = ablation, without_cm, ablation_temperature
        for y, model in zip(TARGETS, cand):
            model.save_model(os.path.join(cand_dir, f'model_{y}.txt'))
    reference_temperature = max(temps, key=lambda t: trifecta_score(reference, validation, t)[0])
    c_scores, c_hits, dates = trifecta_score(reference, holdout, reference_temperature, details=True)
    n_scores, n_hits, _ = trifecta_score(cand, holdout, temperature, details=True)
    check = evaluation.compare(c_scores, n_scores, dates, gain=MIN_GAIN)
    c_ll, n_ll, n = float(np.mean(c_scores)), float(np.mean(n_scores)), len(c_scores)
    c_hit, n_hit = float(np.mean(c_hits))*100, float(np.mean(n_hits))*100
    quality_passed = check['approved'] and n_hit >= c_hit
    import profit_gate
    if quality_passed:
        profit_gate.stage(MODEL, cand_dir, temperature, True, cut, now_jst().isoformat())
    win = False  # Promotion requires subsequent paired market returns.
    check['reason'] += '／締切前オッズによる候補の新規収支確認待ち'
    # Consume even a failed comparison: repeated tries must not reuse holdout.
    evaluation.consume(state_path, holdout['date'].max())
    imp = {}
    for y, m in zip(TARGETS, cand):
        for f_, v in zip(m.feature_name(), m.feature_importance('gain')):
            imp[f_] = imp.get(f_, 0) + v
    top = sorted(imp.items(), key=lambda x: -x[1])[:12]
    res = dict(cut=cut, races=n, n_feats=len(X2), temperature=temperature,
               use_cm=int(use_cm), cm_validation_ll=cm_validation_ll,
               no_cm_validation_ll=no_cm_validation_ll,
               holdout_low95=check['low95'], holdout_high95=check['high95'],
               promotion_reason=check['reason'], use_bf=int(use_bf),
               ev_cov=round(float(ev_cov), 2), bf_days=int(tr_days),
               top=' '.join(k for k, _ in top), champ_ll=round(c_ll, 4), cand_ll=round(n_ll, 4),
               champ_hit=round(c_hit, 1), cand_hit=round(n_hit, 1),
               adopted=int(win))
    shutil.rmtree(cand_dir, ignore_errors=True)
    return res


def main():
    now = now_jst()
    today = now.date()
    yday = today - datetime.timedelta(days=1)
    full = os.environ.get('MANUAL') == '1' or today.weekday() == 0
    import discover_sources
    try:
        discover_sources.main()
    except Exception as e:
        print('公式取得先の確認に失敗', type(e).__name__)
    train.download_all()
    download_b_all()
    # 成績ファイルの取りこぼしが多いと、成績表がおかしくなるので何も変えない
    expect = (yday - train.START).days + 1
    have = sum(os.path.exists(k_path(train.START + datetime.timedelta(days=i)))
               for i in range(expect))
    if have < expect * 0.9:
        notify('夜間の改善エラー', f'成績ファイルが足りません（{have}/{expect}日）。'
               '今回は何も変更していません')
        raise SystemExit(1)
    df, rc = train.load_all()
    if len(df) == 0:
        notify('夜間の改善エラー', '成績データを1件も取得できませんでした（何も変更していません）')
        raise SystemExit(1)
    df = prepare(df, rc)
    last = int(df['date'].max())
    import profit_gate
    profit_result = profit_gate.review(MODEL, 'logs', now.isoformat())
    print('候補モデルの収支確認:', profit_result['reason'])
    write_cur(df)
    setsu.write_tables(df, MODEL)
    extra.write_tables(df, MODEL, f'{today:%Y%m%d}')
    lines = [f'選手・モーターの成績表と今節の成績表を {last // 10000}/{last // 100 % 100}/'
             f'{last % 100} までの結果で更新しました（{len(df):,}走）']
    lines.append('候補モデルの収支確認: ' + profit_result['reason'] + ('（モデルを採用・旧版保存済み）' if profit_result['approved'] else ''))
    hot = setsu.hot_list(setsu.load_tables(MODEL), f'{today:%Y%m%d}')
    if hot:
        names = {}
        try:
            from common import VENUES
            names = VENUES
        except ImportError:
            pass
        lines.append('今節好調（3走以上・平均着順の良い順）：')
        for avg, t3, racer, venue, n in hot:
            lines.append(f'  {names.get(venue, venue)} 登録番号{racer}：'
                         f'{n}走 平均{avg:.1f}着 3着内{t3 * 100:.0f}%')
    if not os.path.exists(k_path(yday)):
        lines.append(f'※{yday:%m/%d}の成績ファイルがまだ無いので、その日は次の夜に入ります')
    row = dict(date=f'{today:%Y%m%d}', run_at=f'{now:%H:%M}', data_until=last,
               starts=len(df), cut='', races='', champ_ll='', cand_ll='',
               champ_hit='', cand_hit='', adopted='')
    if full and os.path.exists(os.path.join(MODEL, 'shadow', 'manifest.json')):
        lines.append('固定候補の収支比較を継続中。比較中の候補を作り直す学習は省略します。')
    elif full:
        try:
            r = challenge(add_all(df, rc), today)
            row.update({k: v for k, v in r.items() if k in row})
            det = os.path.join('logs', 'nightly_detail.csv')
            details = pd.DataFrame([dict(date=row['date'], run_at=row['run_at'], **r)])
            if os.path.exists(det):
                details = pd.concat([pd.read_csv(det), details], ignore_index=True)
            details.to_csv(det, index=False)
            lines.append(f'新しい材料（今節・フライング歴・当日の前走・級別・全国/当地勝率・'
                         f'モーター/ボート2連率・年齢・体重・風向・一周/まわり足/直線・選手コメント'
                         f'{"・直前情報" if r["use_bf"] else ""}、計{r["n_feats"]}個）を入れたモデルを、'
                         f'直近{EVAL_DAYS}日（{r["races"]}レース）で比較：')
            lines.append(f'  当てはまり 今{r["champ_ll"]} → 新{r["cand_ll"]}（大きいほど良い）')
            lines.append(f'  本命の的中率 今{r["champ_hit"]}% → 新{r["cand_hit"]}%')
            lines.append('  → 新しいモデルに入れ替えました（前のモデルは保存済み）'
                         if r['adopted'] else '  → 本番モデル維持：' + r.get('promotion_reason', '新規収支確認待ち'))
        except Exception as e:
            lines.append(f'新しいモデルの比較に失敗（今のモデルのまま）：{e!r}')
    try:
        used, got = backfill.main()
        lines.append(f'直前情報の過去分を取りため：{used}ページ読み、'
                     f'これまでに{backfill.coverage()}日分そろいました'
                     '（60日分以上そろうと、展示ST・チルト・部品交換なども比べます）')
    except Exception as e:
        lines.append(f'直前情報の取りためで失敗：{e!r}')
    try:
        u = backfill.comments()
        lines.append(f'選手コメントの過去分（下関・唐津）を取りため：{u}ページ')
    except Exception as e:
        lines.append(f'選手コメントの取りためで失敗：{e!r}')
    try:
        n = build_wind_map(rc)
        lines.append(f'風向の対応表：場×向き {n}通りを確認済み')
    except Exception as e:
        lines.append(f'風向の対応表を作れず：{e!r}')
    os.makedirs('logs', exist_ok=True)
    new = not os.path.exists(LOG)
    pd.DataFrame([row]).to_csv(LOG, mode='a', header=new, index=False)
    print('\n'.join(lines))
    notify('夜間の改善', '\n'.join(lines))


if __name__ == '__main__':
    main()
