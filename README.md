# boatrace-ai

公式データとLightGBMで3連単を推定する個人用システムです。

現在の運用・保存場所・復旧方法・既知の課題は [運用引き継ぎ](docs/OPERATIONS.md) を参照してください。

## 実行

GASがGitHub Actionsのworkflow_dispatchを起動します。predictは締切15分以内に確認し、送信は2分以上前のみ。AI上位4点と判断ルール4点に共通する買い目のうち、各点のAI確率12%以上・AI推定EV1.0以上を最大2点、1日1回だけ通知します。朝候補や見送りの通知は出しません。

判断ルールはChatGPTの分析手順の一部を数値化したものです。手動の4点・仮想1万円方式、固定67候補の比較とは分けて記録します。統合予想の回収改善は未実証です。[通知の仕様と再学習](docs/CONSENSUS_NOTICE.md)を参照してください。モデル確率と実際の的中率はlogs/calibration.csv等で検証します。

## 回帰テスト

```sh
pip install -r requirements.txt pytest
python -m pytest -q
```

テストでは外部通信・通知・モデル更新を行いません。GitHub Actionsのvalidateでも同じチェックを実行します。

秘密情報はGitHub Secrets/GASプロパティに保持し、リポジトリには保存しません。
