"""バックテスト用の注文表現と Bar モード約定判定（仕様 3 章）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cryptobot.core.types import Bar, OrderType, Side
from cryptobot.strategies.base import Urgency


@dataclass(slots=True)
class BtOrder:
    order_id: int
    instrument_key: str
    side: Side
    qty: float  # 未約定残
    order_type: OrderType
    limit_price: float | None
    urgency: Urgency
    created_bar: int  # 発注したバー番号（このバーの close で発注）
    strategy: str = ""
    bars_open: int = 0  # 市場に置かれてから経過したバー数
    filled_qty: float = 0.0
    reprices: int = 0
    is_reduce_only: bool = False
    ref_price: float = 0.0  # 発注時の参照価格（スリッページ計測用）
    events: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BtFill:
    order_id: int
    instrument_key: str
    strategy: str
    side: Side
    qty: float
    price: float
    is_maker: bool
    fee: float
    ts_ms: int
    ref_price: float
    bars_to_fill: int

    @property
    def slippage_bp(self) -> float:
        """参照価格に対する不利方向の乖離（bp）。負なら有利。"""
        if self.ref_price <= 0:
            return 0.0
        return self.side.sign * (self.price / self.ref_price - 1) * 1e4


@dataclass(frozen=True, slots=True)
class FillModelConfig:
    participation: float = 0.05  # 1 バーで約定できる出来高比率の上限
    half_spread_bp: float = 0.5  # 成行の片側スプレッド（bp）
    impact_k: float = 0.1  # impact = k * sqrt(notional / bar_quote_volume)
    patience_bars: int = 3  # 指値を寄せ直すまでの未約定バー数
    escalate_bars: int = 5  # NORMAL: 成行へ切替
    giveup_bars: int = 10  # LOW: 取消


def market_fill_price(side: Side, bar: Bar, notional: float, cfg: FillModelConfig) -> float:
    """成行は次バーの始値 ± (半スプレッド + インパクト)。"""
    impact = 0.0
    if bar.quote_volume > 0 and notional > 0:
        impact = cfg.impact_k * math.sqrt(notional / bar.quote_volume)
    slip = cfg.half_spread_bp / 1e4 + impact
    return bar.open * (1 + side.sign * slip)


def try_fill(order: BtOrder, bar: Bar, cfg: FillModelConfig) -> tuple[float, float, bool] | None:
    """バー bar に対して注文の約定を判定する。戻り値 (数量, 価格, is_maker) か None。

    指値（post-only）の保守的判定: 買いは low < limit（同値タッチは不約定）、売りは high > limit。
    部分約定: 1 バーあたり volume × participation まで。
    """
    max_qty = bar.volume * cfg.participation
    if max_qty <= 0:
        return None
    qty = min(order.qty, max_qty)
    if order.order_type is OrderType.MARKET:
        price = market_fill_price(order.side, bar, qty * bar.open, cfg)
        return qty, price, False
    assert order.limit_price is not None
    lp = order.limit_price
    if order.side is Side.BUY and bar.low < lp:
        return qty, min(lp, bar.open) if bar.open < lp else lp, True
    if order.side is Side.SELL and bar.high > lp:
        return qty, max(lp, bar.open) if bar.open > lp else lp, True
    return None
