# たかさん日本株分析 v2 API（簡単構成）

この版は GitHub のルートに全ファイルを置くだけで動く初心者向け構成です。

## ファイル構成

```text
main.py
models.py
scoring.py
provider.py
requirements.txt
render.yaml
README.md
```

## Render設定

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
uvicorn main:app --host 0.0.0.0 --port $PORT
```

## 動作確認

公開URLが

```text
https://xxxx.onrender.com
```

なら、

```text
https://xxxx.onrender.com/health
```

で

```json
{"status":"ok"}
```

が出れば成功です。

次に、

```text
https://xxxx.onrender.com/docs
```

を開き、

GET /analyze/{ticker}

で 6857 をテストしてください。

## Growth Score確認

POST /score/growth に以下を入力:

```json
{
  "revenue_growth_pct": 20,
  "operating_profit_growth_pct": 15,
  "eps_growth_pct": 18,
  "operating_margin_pct": 10
}
```

47.9 が返ればOKです。

## 重要

現在の6857は Sites/API接続テスト用のデータです。
未登録銘柄について架空の数値は返しません。
