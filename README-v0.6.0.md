# たかさん日本株分析 API v0.6.0
## 銘柄別・自動DCF前提生成版

### 変更点
- v0.5.1の安定したデータ取得ロジックを維持
- 全銘柄共通の固定DCF前提を廃止
- 実績の売上成長率 / 営業利益成長率 / EPS成長率 / 営業利益率
  / Growth Score / データ取得率からDCF前提を自動生成
- 弱気・標準・強気の3シナリオを銘柄ごとに作成
- 標準DCFを基準にMispricing Scoreを計算
- 自動DCF前提の根拠を `dcf.scenarios.assumption_engine` に返す
- データ不足時は従来通り推測しない

### 自動DCF前提の考え方
- 実績成長率は極端値を抑制
- FCF成長率は保守的に上限を設定
- 営業利益率が高い企業は標準成長率を少し上方補正
- データ取得率が低いほど割引率を高める
- WACCの厳密推計ではなく、銘柄比較用のルールベース

### GitHubで差し替えるファイル
- main.py
- scoring.py
- provider.py
- models.py
- requirements.txt
- render.yaml

### 確認
1. Render再デプロイ
2. `/health` → 0.6.0
3. `/analyze/6857`
4. `dcf.scenarios.assumption_engine`
5. bear / base / bull の成長率・割引率・理論株価を確認
