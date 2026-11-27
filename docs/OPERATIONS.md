# 運用引き継ぎ（2026-11-27）

対象: pocha2525/boatrace-ai。既存GitHub Actions / GAS / ntfy / Sheetsを継続。秘密情報を公開リポジトリへ移さない。

## 現行方式

LightGBM 3モデル（各66特徴量）。本命確率12%以上・期待値1.0以上、2点目期待値0.8以上、最大2点。締切15分以内に判定し、送信直前に2分以上残っている場合だけ通知する。ChatGPTの4点・仮想1万円方式とは別。モデルや閾値は今回変更しない。

## 引き継ぎで確認したこと

mainのコード・現行3モデル・cur表・logs・hist・workflowを取得。predict run 45980842813の成功、14場取得・判定・ログ保存を確認。GAS「ボートボート」のkick.gs / sheet.gsと2トリガーを画面で確認。kickは19:17:28、同期は19:17:00の実行表示。同期エラー率0%、kick累積0.35%。

## 修正と検証

朝の出走表を後段へ渡し、取得状態をスレッドごとに分離。朝は展示・コメントを取得せず暫定予測。出走ログを排他書き込み。展示ST欠損を扱い、未取得の風・波を0にしない。過去展示は6艇のST・タイムがそろったレースだけ取得済み。結果未取得を返還・中止と断定せず再照合する。

検証: `pip install -r requirements.txt pytest`、`python -m pytest -q`。現行モデルによる朝予測を含む10件。外部通信・通知・購入・モデル更新なし。validate workflowでも同じテストを実行する。

## 保存先と復旧

本体はrootのPythonとot/、実行定義は.github/workflows/。モデルはmodel/model_{win,top2,top3}.txt、旧版はmodel/archive/、履歴はmodel/history.csv。cur表はmodel/cur_*.csv。通知はlogs/notify_YYYYMMDD.csv、採点対象はlogs/YYYYMMDD.csv、結果はlogs/results_YYYYMMDD.csv。過去展示はhist/before/、コメントはhist/comment/。

成績・番組表data/K・data/BはActionsのkdata-キャッシュ。恒久保存ではなく、消失時は既存取得処理で再取得。モデル復旧はarchiveの3本を同時に戻し履歴を記録する。NTFY_TOPIC / RELAY_URLはGitHub Secrets、GH_TOKEN等はGASプロパティの既存値を再利用する。

## 継続改善の課題

展示ST等のモデル採用は評価期間80%以上・学習60日以上。未対応の独自展示7場・コメント21場、コメント辞書精度、確率校正、公式返還を含む会計、本番進入/本番気象と展示時点情報の差、同じ28日を繰り返すモデル選択を継続検証する。

10分トリガーとActions待ち時間により全レースの締切前通知は保証しない。ntfy受付は端末到着・実購入の証明ではない。通知後のgit保存失敗に対する永続台帳も今後の改善対象。

GASのsetup()は全トリガーを削除する旧実装のため再実行しない。kick / シート同期を維持する。trainの自動起動は停止のまま。nightlyの月曜・手動比較は別処理。
