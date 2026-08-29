from providers.base import StockDataProvider


class MockProvider(StockDataProvider):
    """
    Sitesとの接続確認だけに使う最小Provider。
    未登録銘柄について架空の数値は生成しない。
    """

    _data = {
        "6857": {
            "company_name": "アドバンテスト",
            "fiscal_period": "接続テスト用データ",
            "revenue_growth_pct": 32.8,
            "operating_profit_growth_pct": 81.7,
            "eps_growth_pct": 65.4,
            "operating_margin_pct": 28.6,
            "latest_growth_pct": 32.8,
            "previous_growth_pct": 24.6,
            "margin_change_points": 4.6,
            "guidance_revision_pct": 0.0,
        }
    }

    def get_company_snapshot(self, ticker: str):
        key = ticker.replace(".T", "")
        return self._data.get(key)
