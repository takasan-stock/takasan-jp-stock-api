# たかさん日本株分析 API v0.5.1 — データ取得安定版

v0.4.0で6857の `free_cash_flow / net_debt / shares_outstanding` を取得できた
provider.py を復活させ、その上に v0.5 系の機能を載せた統合版です。

## 主な変更
- v0.4.0の実績あるデータ取得ロジックを復活
- DCFを弱気 / 標準 / 強気の3シナリオ化
- 標準DCFを基準に Mispricing Score を自動計算
- DCF未計算時に `dcf.missing_fields` で不足項目を明示
- Growth / Changeのロジックはv0.4系を維持
- AI Scoreは Growth 50% / Change 30% / Mispricing 20%
  - 未取得スコアがあれば、取得済みだけで重みを再正規化
- 架空値は生成しない

## DCFシナリオ
- 弱気: FCF成長率5% / 割引率9% / 永久成長率0.5%
- 標準: FCF成長率10% / 割引率8% / 永久成長率1.0%
- 強気: FCF成長率15% / 割引率7% / 永久成長率1.5%

## GitHubで差し替えるファイル
- main.py
- provider.py
- scoring.py
- models.py
- requirements.txt
- render.yaml

## 確認手順
1. Render再デプロイ
2. `/health` → version `0.5.1`
3. `/docs`
4. `GET /analyze/6857`
5. `missing_fields: []` を確認
6. `dcf.scenarios.scenarios.bear/base/bull` を確認
7. `mispricing` を確認
