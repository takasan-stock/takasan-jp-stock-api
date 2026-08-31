# たかさん日本株分析 API v0.4.0 — DCF実データ強化版

差し替え対象: main.py / provider.py / requirements.txt
models.py / scoring.py も同梱しています。

改善:
- shares_outstanding を info → fast_info → quarterly BS → annual BS の順で取得
- net_debt を info → Net Debt → Total Debt - Cash の順で取得
- FCF を quarterly / annual cashflow の両方から取得
- 欠損は推測せず null
- 正のFCFと発行株式数がそろった場合のみDCFを計算

推奨テスト: 6857, 9563
