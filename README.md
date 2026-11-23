# boatrace-ai

ボートレースの3連単を予想し、期待値の高い買い目をスマホに通知する個人用ツールです。

## 動き方
| 仕組み | いつ動くか | やること |
|---|---|---|
| predict | 日本時間 8:00〜21:50 に10分ごと | 締切13分以内のレースを予想し、期待値1.2以上の買い目を通知。`logs/` に記録 |
| train | 毎週月曜 4:23 | 2023年1月〜前日の競走成績を取得し、モデルを学習し直して `model/` に保存 |
| report | 毎朝 8:07 | 前日と累計の的中率・回収率を通知 |

## ファイル
- `predict.py` / `train.py` / `report.py` / `common.py`：本体
- `.github/workflows/`：自動実行の設定

## 最初の設定
1. Settings → Secrets and variables → Actions → New repository secret
   - Name: `NTFY_TOPIC`
   - Secret: ntfyアプリで登録したトピック名
2. Actions → train → Run workflow（初回は30分ほどかかります）
3. Actions → predict → Run workflow で動作確認（「動作確認OK」の通知が届きます）

## 注意
- データは公式サイト（boatrace.jp / mbrace.or.jp）から取得しています。アクセスは必要最小限にしています
- GitHubの自動実行は混雑で遅れることがあり、その場合は予想が出ないレースがあります
- 期待値ルールで実際に勝てるかは未検証です。成績レポートで十分なレース数を確認してから使ってください
