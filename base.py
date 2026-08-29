from abc import ABC, abstractmethod


class StockDataProvider(ABC):
    @abstractmethod
    def get_company_snapshot(self, ticker: str):
        raise NotImplementedError
