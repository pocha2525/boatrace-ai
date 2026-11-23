"""過去データの取得 → 特徴量の計算 → モデルの学習 → model/ に保存"""
import os
import glob
import datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import lightgbm as lgb

from common import (DATA, MODEL, FEATS, X, TARGETS, now_jst, k_path,
                    download_k, read_k, notify, add_rel)

START = datetime.date(2023, 1, 1)
TRAIN_FROM = 20230401  # 選手の成績がある程度たまってから学習に使う


def download_all():
    end = now_jst().date() - datetime.timedelta(days=1)
    days = []
    d = START
    while d <= end:
        if not os.path.exists(k_path(d)):
            days.append(d)
        d += datetime.timedelta(days=1)
    print('未取得の日数', len(days))
    with ThreadPoolExecutor(3) as ex:
        for i, _ in enumerate(ex.map(download_k, days)):
            if i % 100 == 0:
                print('ダウンロード', i, '/', len(days))


def load_all():
    rows, races = [], []
    for p in sorted(glob.glob(os.path.join(DATA, 'K', 'K*.TXT'))):
        r, rc = read_k(p)
        rows += r
        races += rc
    df = pd.DataFrame(rows)
    rc = pd.DataFrame(races)
    print('レース数', len(rc), '/ 出走数', len(df))
    return df, rc


def past(df, keys, col, name):
    """その日より前の成績だけを使った率を計算する。"""
    k = keys + ['date']
    g = df.groupby(k)[col].agg(['sum', 'count']).reset_index()
    g = g.sort_values('date')
    cs = g.groupby(keys)['sum'].cumsum() - g['sum']
    cc = g.groupby(keys)['count'].cumsum() - g['count']
    g[name] = cs / cc.where(cc >= 5)
    return df.merge(g[k + [name]], on=k, how='left')


def main():
    os.makedirs(MODEL, exist_ok=True)
    download_all()
    df, rc = load_all()
    if len(df) == 0:
        notify('学習エラー', '成績データを1件も取得できませんでした')
        raise SystemExit(1)
    df = df.dropna(subset=['course'])
    df['course'] = df['course'].astype(int)
    df['year'] = df['date'] // 10000
    df['win'] = (df['rank'] == 1).astype(int)
    df['top2'] = (df['rank'] <= 2).astype(int)
    df['top3'] = (df['rank'] <= 3).astype(int)
    df['st_ok'] = df['st'].where(df['st'] < 1)

    # 今日の予想用：全期間の成績表を保存
    for keys, col, name in FEATS:
        g = df.groupby(keys)[col].agg(['sum', 'count'])
        g[name] = g['sum'] / g['count'].where(g['count'] >= 5)
        g[[name]].reset_index().to_csv(
            os.path.join(MODEL, f'cur_{name}.csv'), index=False)

    # 学習用：その日より前の成績だけで特徴量を作る
    for keys, col, name in FEATS:
        df = past(df, keys, col, name)
    keys = ['date', 'venue', 'race']
    df = df.merge(rc[keys + ['wind', 'wave']], on=keys, how='left')
    df = add_rel(df, keys)
    one = df[df['course'] == 1][keys + ['rc_win_d']]
    one = one.drop_duplicates(keys).rename(columns={'rc_win_d': 'in_d'})
    df = df.merge(one, on=keys, how='left')
    tr = df[df['date'] >= TRAIN_FROM]
    print('学習データ', len(tr))

    for y in TARGETS:
        model = lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.05, num_leaves=63,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            verbose=-1)
        model.fit(tr[X], tr[y], categorical_feature=['venue'])
        model.booster_.save_model(os.path.join(MODEL, f'model_{y}.txt'))
        print(y, '学習完了')
    now = now_jst()
    info = (f'{now:%Y-%m-%d %H:%M} JST / '
            f'{df["date"].min()}〜{df["date"].max()} / {len(tr)}行')
    with open(os.path.join(MODEL, 'trained_at.txt'), 'w') as f:
        f.write(info + '\n')
    # モデルの切り替え履歴（成績レポートでモデルごとに集計するため）
    path = os.path.join(MODEL, 'history.csv')
    new = not os.path.exists(path)
    with open(path, 'a', encoding='utf-8') as f:
        if new:
            f.write('from,version,detail\n')
        f.write(f'{now:%Y%m%d%H%M},v{now:%m%d} 自動学習,{info}\n')
    print('保存完了')
    with open(os.path.join(MODEL, 'trained_at.txt')) as f:
        notify('学習完了', f.read())


if __name__ == '__main__':
    main()
