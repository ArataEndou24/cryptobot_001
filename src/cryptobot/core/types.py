"""基本型。研究・バックテスト・本番で共通に使う不変データ。

設計方針:
- すべて frozen dataclass（イベントは不変）。
- 数量・価格は float（研究用途）。本番の発注直前で InstrumentSpec に従い Decimal 丸めを行う。
- 取引所固有の情報は `venue` フィールドで識別し、型自体は取引所非依存にする。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class InstrumentType(StrEnum):
    SPOT = "spot"
    PERPETUAL = "perpetual"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForce(StrEnum):
    GTC = "gtc"  # 約定するまで有効
    IOC = "ioc"  # 即時約定分のみ、残りは取消
    POST_ONLY = "post_only"  # メイカーになれない場合は拒否


class OrderStatus(StrEnum):
    """OMS の状態機械。UNKNOWN を第一級の状態として扱う（通信断で結果不明）。"""

    NEW = "new"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED)


@dataclass(frozen=True, slots=True)
class Instrument:
    """取引対象の識別子。"""

    venue: str  # 例: "binance_um", "bybit_linear", "hyperliquid"
    symbol: str  # 取引所表記。例: "BTCUSDT"
    base: str  # 例: "BTC"
    quote: str  # 例: "USDT"
    type: InstrumentType

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.symbol}"


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """取引所が定める数量・価格の制約。小資金では最小数量が最大の制約になる。"""

    instrument: Instrument
    tick_size: Decimal  # 価格刻み
    step_size: Decimal  # 数量刻み
    min_qty: Decimal  # 最小数量
    min_notional: Decimal  # 最小名目額（quote 建て）
    max_leverage: int
    maker_fee: Decimal  # 例: Decimal("0.0002")
    taker_fee: Decimal

    def round_price(self, price: float | Decimal) -> Decimal:
        return (Decimal(str(price)) / self.tick_size).quantize(
            Decimal(1), ROUND_DOWN
        ) * self.tick_size

    def round_qty(self, qty: float | Decimal) -> Decimal:
        return (Decimal(str(qty)) / self.step_size).quantize(
            Decimal(1), ROUND_DOWN
        ) * self.step_size

    def is_valid_order(self, qty: Decimal, price: Decimal) -> tuple[bool, str]:
        if qty < self.min_qty:
            return False, f"qty {qty} < min_qty {self.min_qty}"
        if qty * price < self.min_notional:
            return False, f"notional {qty * price} < min_notional {self.min_notional}"
        return True, ""


@dataclass(frozen=True, slots=True)
class Trade:
    """約定（ティック）。"""

    instrument_key: str
    ts_ms: int  # 取引所のイベント時刻
    price: float
    qty: float
    is_buyer_maker: bool  # True なら売り成行（テイカーが売り手）
    trade_id: int

    @property
    def taker_side(self) -> Side:
        return Side.SELL if self.is_buyer_maker else Side.BUY


@dataclass(frozen=True, slots=True)
class Bar:
    """バー。時間バー・出来高バー・ドルバーで共通。

    open_ts_ms は区間開始、close_ts_ms は区間終了（この時刻まで待って初めてバーが「確定」する）。
    特徴量は close_ts_ms 以降でしか使えない。
    """

    instrument_key: str
    open_ts_ms: int
    close_ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float  # base 建て出来高
    quote_volume: float  # quote 建て出来高
    trade_count: int
    taker_buy_volume: float  # 買い成行の base 出来高
    vwap: float

    @property
    def taker_sell_volume(self) -> float:
        return self.volume - self.taker_buy_volume

    @property
    def taker_imbalance(self) -> float:
        """(買い成行 − 売り成行) / 出来高。−1〜+1。"""
        if self.volume <= 0:
            return 0.0
        return (2 * self.taker_buy_volume - self.volume) / self.volume


@dataclass(frozen=True, slots=True)
class FundingRate:
    instrument_key: str
    ts_ms: int  # funding 適用時刻
    rate: float  # 1 回あたり。正ならロングがショートへ支払う
    interval_hours: int


@dataclass(frozen=True, slots=True)
class OpenInterest:
    instrument_key: str
    ts_ms: int
    open_interest: float  # base 建て
    open_interest_value: float  # quote 建て
