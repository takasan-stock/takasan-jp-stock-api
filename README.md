# たかさん日本株分析 v2 API

ChatGPT Sites から日本株分析ロジックを呼び出すための最小APIスターターです。

## 目的

Sites:
- Dashboard
- 一括分析
- 決算Momentum
- Growth Score
- Change Score
- DCF
- AI総合分析

API:
- 数値計算
- 1銘柄一括分析
- 将来の実データ接続

という役割分担にします。

---

## 1. ローカル起動

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

ブラウザ:

- API: http://127.0.0.1:8000
- API仕様書: http://127.0.0.1:8000/docs

---

## 2. エンドポイント

### GET /health

接続確認。

### POST /score/growth

入力例:

```json
{
  "revenue_growth_pct": 20,
  "operating_profit_growth_pct": 15,
  "eps_growth_pct": 18,
  "operating_margin_pct": 10
}
```

結果:

Growth Score = 47.9

### POST /score/change

```json
{
  "latest_growth_pct": 30,
  "previous_growth_pct": 15,
  "margin_change_points": 2.5,
  "guidance_revision_pct": 10
}
```

### POST /score/mispricing

```json
{
  "fair_value": 2500,
  "market_price": 1800,
  "growth_score": 80,
  "data_coverage_pct": 95
}
```

### POST /dcf

```json
{
  "base_free_cash_flow": 100,
  "growth_rate_pct": 10,
  "discount_rate_pct": 8,
  "terminal_growth_rate_pct": 2,
  "years": 5,
  "net_debt": 20,
  "shares_outstanding": 50
}
```

金額単位は全入力で統一してください。

### GET /analyze/6857

Sitesとの接続確認用。

現在は6857だけテストデータを返します。
未登録銘柄には架空データを作らず404を返します。

---

## 3. Sites側の呼び出しイメージ

```javascript
const API_BASE = "https://YOUR-API.example.com";

async function analyzeTicker(ticker) {
  const response = await fetch(
    `${API_BASE}/analyze/${encodeURIComponent(ticker)}`
  );

  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "分析に失敗しました");
  }

  return await response.json();
}
```

分析ボタンで `analyzeTicker("6857")` を実行し、
返ってきたJSONを現在のDashboardカードに表示します。

---

## 4. Renderへ公開

1. GitHubにこのフォルダをアップロード
2. RenderでNew Web Service
3. GitHubリポジトリを選択
4. Build Command:
   `pip install -r requirements.txt`
5. Start Command:
   `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. Deploy

`render.yaml` を利用してBlueprintから作成してもOKです。

公開後:

`https://xxxx.onrender.com/health`

が

```json
{"status":"ok"}
```

なら成功です。

---

## 5. 次の段階

現在の `providers/mock.py` を実データProviderへ差し替えます。

候補:

- 既存Google Sheets
- J-Quants
- TDnet / EDINET
- 現在のscan.pyで生成したCSV/JSON
- 独自DB

おすすめは最初に、

`scan.py → JSON/Google Sheets → API → Sites`

の一本道を完成させることです。

その後:

- 決算Momentumランキング
- TDnet Change Detector
- 決算資料AI Reader
- 上方修正Predictor
- RS Score
- 5%ルール
- Mispricing
- AI総合ランキング

を追加します。

---

## 重要

`score_change` と `score_mispricing` は v2 APIスターター用の暫定式です。
既存の本番ロジックが確定したら `services/scoring.py` の関数だけを差し替えます。

Growth Scoreは現在確認できている挙動
（20%,15%,18%,10% → 47.9）
に合わせています。
