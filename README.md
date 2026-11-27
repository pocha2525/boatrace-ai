# boatrace-ai

公式データとLightGBMで3連単を推定する個人用システムです。

現在の運用・保存場所・復旧方法・既知の課題は [運用引き継ぎ](docs/OPERATIONS.md) を参照してください。

## 実行

GASがGitHub Actionsのworkflow_dispatchを起動します。predictは締切15分以内、送信は2分以上前のみ。本命のモデル確率12%以上・期待値1.0以上、2点目は期待値0.8以上、最大2点です。

ChatGPTの4点・仮想1万円方式とは別です。モデル確率と実際の的中率はlogs/calibration.csv等で検証します。

## 回帰テスト

```sh
pip install -r requirements.txt pytest
python -m pytest -q
```

テストでは外部通信・通知・モデル更新を行いません。GitHub Actionsのvalidateでも同じチェックを実行します。

秘密情報はGitHub Secrets/GASプロパティに保持し、リポジトリには保存しません。
