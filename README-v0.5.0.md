# たかさん日本株分析 API v0.5.0

## 主な追加
- DCFを弱気・標準・強気の3シナリオ化
- `/analyze/{ticker}` に `dcf.scenarios` と `fair_value_range` を追加
- 標準DCF理論株価と現在株価から Mispricing Score を自動計算
- 欠損値を推測で補完しない方針を維持

## DCF初期前提
| scenario | FCF growth | discount | terminal |
|---|---:|---:|---:|
| 弱気 | 5% | 9% | 0.5% |
| 標準 | 10% | 8% | 1.0% |
| 強気 | 15% | 7% | 1.5% |

この前提は暫定値です。業種・企業ごとの成長性や資本コストに応じた調整が必要です。

## GitHub差し替え
`main.py`, `models.py`, `provider.py`, `scoring.py`, `requirements.txt`, `render.yaml` をアップロードして置換してください。

Render再デプロイ後:
1. `/health` で version `0.5.0`
2. `/docs`
3. `GET /analyze/6857`
4. `dcf.scenarios` と `mispricing` を確認
