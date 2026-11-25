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


def trifecta_score(models, ev):
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
    lls, hits = [], []
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
        lls.append(np.log(max(p[(a, b, c)], 1e-9)))
        hits.append(max(p, key=p.get) == (a, b, c))
    return float(np.mean(lls)), float(np.mean(hits)) * 100, len(lls)


def challenge(df, today):
    """挑戦者モデルを作り、直近28日で今のモデルと比べる。良ければ入れ替える"""
    cut = int((today - datetime.timedelta(days=EVAL_DAYS)).strftime('%Y%m%d'))
    X2 = X + setsu.SETSU   # 今の材料 ＋ 今節の材料
    tr = df[(df['date'] >= train.TRAIN_FROM) & (df['date'] < cut)]
    ev = df[df['date'] >= cut]
    cand_dir = os.path.join(MODEL, 'candidate')
    os.makedirs(cand_dir, exist_ok=True)
    cand = []
    for y in TARGETS:
        m = lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.05, num_leaves=63,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            verbose=-1)
        m.fit(tr[X2], tr[y], categorical_feature=['venue'])
        path = os.path.join(cand_dir, f'model_{y}.txt')
        m.booster_.save_model(path)
        cand.append(lgb.Booster(model_file=path))
        print(y, '挑戦者 学習完了')
    champ = [lgb.Booster(model_file=os.path.join(MODEL, f'model_{y}.txt'))
             for y in TARGETS]
    c_ll, c_hit, n = trifecta_score(champ, ev)
    n_ll, n_hit, _ = trifecta_score(cand, ev)
    win = n_ll - c_ll >= MIN_GAIN and n_hit >= c_hit
    res = dict(cut=cut, races=n, champ_ll=round(c_ll, 4), cand_ll=round(n_ll, 4),
               champ_hit=round(c_hit, 1), cand_hit=round(n_hit, 1),
               adopted=int(win))
    if win:
        arc = os.path.join(MODEL, 'archive', f'{today:%Y%m%d}')
        os.makedirs(arc, exist_ok=True)
        for y in TARGETS:
            shutil.copy(os.path.join(MODEL, f'model_{y}.txt'), arc)
            shutil.copy(os.path.join(cand_dir, f'model_{y}.txt'),
                        os.path.join(MODEL, f'model_{y}.txt'))
        now = now_jst()
        with open(os.path.join(MODEL, 'history.csv'), 'a', encoding='utf-8') as f:
            f.write(f'{now:%Y%m%d%H%M},v{now:%m%d} 夜間更新,'
                    f'学習〜{cut}前日 / 直近{EVAL_DAYS}日 当てはまり{c_ll:.4f}→{n_ll:.4f}\n')
    shutil.rmtree(cand_dir, ignore_errors=True)
    return res


def main():
    now = now_jst()
    today = now.date()
    yday = today - datetime.timedelta(days=1)
    full = os.environ.get('MANUAL') == '1' or today.weekday() == 0
    train.download_all()
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
    write_cur(df)
    setsu.write_tables(df, MODEL)
    lines = [f'選手・モーターの成績表と今節の成績表を {last // 10000}/{last // 100 % 100}/'
             f'{last % 100} までの結果で更新しました（{len(df):,}走）']
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
    if full:
        try:
            r = challenge(setsu.add_hist(features(df, rc)), today)
            row.update(r)
            lines.append(f'今節の材料を足した新しいモデルを、直近{EVAL_DAYS}日（{r["races"]}レース）で比較：')
            lines.append(f'  当てはまり 今{r["champ_ll"]} → 新{r["cand_ll"]}（大きいほど良い）')
            lines.append(f'  本命の的中率 今{r["champ_hit"]}% → 新{r["cand_hit"]}%')
            lines.append('  → 新しいモデルに入れ替えました（前のモデルは保存済み）'
                         if r['adopted'] else '  → 良くならなかったので、今のモデルのままです')
        except Exception as e:
            lines.append(f'新しいモデルの比較に失敗（今のモデルのまま）：{e!r}')
    os.makedirs('logs', exist_ok=True)
    new = not os.path.exists(LOG)
    pd.DataFrame([row]).to_csv(LOG, mode='a', header=new, index=False)
    print('\n'.join(lines))
    notify('夜間の改善', '\n'.join(lines))


if __name__ == '__main__':
    main()
