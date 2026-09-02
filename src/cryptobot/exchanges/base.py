"""取引所アダプタの抽象インターフェース。

設計方針（設計書 2 章・制約分析 1.3）:
- 履歴データ源（HistoricalDataSource）、リアルタイム市場データ（MarketDataStream）、
  発注（TradingVenue）を分離する。研究には前者のみ、本番には三つすべてが必要。
- CEX 系（Bybit 等）と DEX 系（Hyperliquid 等）の両方をこのインターフェースで実装できるよう、
  取引所固有の概念（マージンモード、ポジションモード等）はアダプタ内部に閉じ込める。
- すべての戻り値は core.types の取引所非依存型。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import polars as pl

from cryptobot.core.types import (
    Instrument,
    InstrumentSpec,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)


class HistoricalDataSource(ABC):
    """過去データの取得。ファイル配布（Binance Vision 等）または REST ページング。"""

    venue: str

    @abstractmethod
    def fetch_klines(self, symbol: str, interval: str, day: date) -> pl.DataFrame | None:
        """1 日分の足。列は data.store.KLINE_SCHEMA。存在しなければ None。"""

    @abstractmethod
    def fetch_agg_trades(self, symbol: str, day: date) -> pl.DataFrame | None:
        """1 日分の約定（集約）。列は data.store.AGG_TRADE_SCHEMA。"""

    @abstractmethod
    def fetch_funding(self, symbol: str, year: int, month: int) -> pl.DataFrame | None:
        """1 か月分の資金調達率。列は data.store.FUNDING_SCHEMA。"""


class MarketDataStream(ABC):
    """リアルタイム市場データ（WebSocket）。本番フェーズで実装。"""

    venue: str

    @abstractmethod
    async def trades(self, symbols: list[str]) -> AsyncIterator[dict]:  # type: ignore[type-arg]
        ...

    @abstractmethod
    async def book_top(self, symbols: list[str]) -> AsyncIterator[dict]:  # type: ignore[type-arg]
        ...


class TradingVenue(ABC):
    """発注・照会。本番フェーズで実装。すべてのメソッドは冪等性を意識する。"""

    venue: str

    @abstractmethod
    async def get_spec(self, instrument: Instrument) -> InstrumentSpec: ...

    @abstractmethod
    async def place_order(
        self,
        instrument: Instrument,
        side: Side,
        qty: Decimal,
        order_type: OrderType,
        price: Decimal | None,
        tif: TimeInForce,
        client_order_id: str,
    ) -> OrderStatus: ...

    @abstractmethod
    async def cancel_order(self, instrument: Instrument, client_order_id: str) -> OrderStatus: ...

    @abstractmethod
    async def get_open_orders(self, instrument: Instrument | None = None) -> list[dict]: ...  # type: ignore[type-arg]

    @abstractmethod
    async def get_positions(self) -> dict[str, Decimal]:
        """instrument key → 符号付き数量（ロング正、ショート負）。"""

    @abstractmethod
    async def get_balance(self) -> Decimal:
        """quote 建ての証拠金残高。"""
