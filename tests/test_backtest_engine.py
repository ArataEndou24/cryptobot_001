from decimal import Decimal

import numpy as np
import polars as pl
import pytest

from cryptobot.backtest.engine import BacktestConfig, BacktestEngine
from cryptobot.backtest.orders import BtOrder, FillModelConfig, try_fill
from cryptobot.core.types import Bar, Instrument, InstrumentSpec, InstrumentType, OrderType, Side
from cryptobot.data.store import BAR_SCHEMA
from cryptobot.risk.ladder import DrawdownLadder
from cryptobot.strategies.base import BarContext, Strategy, Target, Urgency

KEY = "hyperliquid:ETH"


def _spec(maker=0.0, taker=0.0) -> InstrumentSpec:
    return InstrumentSpec(
        Instrument("hyperliquid", "ETH", "ETH", "USDC", InstrumentType.PERPETUAL),
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.0001"),
        min_qty=Decimal("0.0001"),
        min_notional=Decimal("10"),
        max_leverage=25,
        maker_fee=Decimal(str(maker)),
        taker_fee=Decimal(str(taker)),
    )


def _bars(closes, volume=1e6, start=1_700_000_000_000, step=3_600_000, gap=None) -> pl.DataFrame:
    """gap=None: 始値は前終値でヒゲあり。gap=x: 始値は前終値×(1+x) でヒゲなし。"""
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1] * (1 + (gap or 0.0))])
    wick = 0.0 if gap is not None else 0.001
    n = len(closes)
    ts = start + np.arange(n) * step
    return pl.DataFrame(
        {
            "open_ts_ms": ts,
            "close_ts_ms": ts + step,
            "open": opens,
            "high": np.maximum(opens, closes) * (1 + wick),
            "low": np.minimum(opens, closes) * (1 - wick),
            "close": closes,
            "volume": np.full(n, volume),
            "quote_volume": np.full(n, volume) * closes,
            "trade_count": np.full(n, 100),
            "taker_buy_volume": np.full(n, volume / 2),
            "vwap": closes,
        },
        schema=BAR_SCHEMA,
    )


class Hold(Strategy):
    name = "hold"

    def __init__(self, exposure: float, urgency=Urgency.HIGH):
        self.exposure, self.urgency = exposure, urgency

    def on_bar(self, ctx: BarContext):
        return [Target(KEY, self.exposure, self.urgency)]


class NoLookahead(Strategy):
    """history が未来を含まないことを検証する戦略。"""

    name = "nolookahead"

    def on_bar(self, ctx: BarContext):
        h = ctx.history(KEY, 1000)
        assert h["close_ts_ms"].max() <= ctx.ts_ms
        assert h["close_ts_ms"].max() == ctx.bars[KEY].close_ts_ms
        return []


def _cfg(cash=1000.0, **fill) -> BacktestConfig:
    return BacktestConfig(
        initial_cash=cash,
        specs={KEY: _spec()},
        fill=FillModelConfig(half_spread_bp=0.0, impact_k=0.0, participation=1.0, **fill),
    )


def test_market_buy_and_hold_hand_calculated():
    # close: 100, 110, 121. bar0 close で発注 → bar1 の open(=100) で約定 → 以降保有
    res = BacktestEngine(_cfg(), [Hold(1.0)], {KEY: _bars([100, 110, 121])}).run()
    assert len(res.fills) == 1
    f = res.fills[0]
    assert f.price == 100.0 and f.qty == pytest.approx(10.0) and not f.is_maker
    eq = res.equity["equity"].to_list()
    assert eq == pytest.approx([1000.0, 1100.0, 1210.0])
    # bar1 の close で目標 1.0 × 1100 / 110 = 10 → 差分 0 → 追加注文なし
    assert res.n_orders == 1
    assert res.summary["total_return"] == pytest.approx(0.21)


def test_no_same_bar_fill_and_no_lookahead():
    res = BacktestEngine(_cfg(), [NoLookahead()], {KEY: _bars([100, 101, 102, 103])}).run()
    assert res.n_orders == 0


def test_limit_post_only_conservative_fill():
    cfg = FillModelConfig(participation=1.0)
    o = BtOrder(1, KEY, Side.BUY, 1.0, OrderType.LIMIT, 100.0, Urgency.NORMAL, 0)
    touch = Bar(KEY, 0, 1, 101, 102, 100.0, 101, 10, 1010, 1, 5, 101)  # low == limit → 不約定
    assert try_fill(o, touch, cfg) is None
    through = Bar(KEY, 0, 1, 101, 102, 99.9, 101, 10, 1010, 1, 5, 101)
    qty, price, maker = try_fill(o, through, cfg)
    assert qty == 1.0 and price == 100.0 and maker
    s = BtOrder(2, KEY, Side.SELL, 1.0, OrderType.LIMIT, 100.0, Urgency.NORMAL, 0)
    assert try_fill(s, Bar(KEY, 0, 1, 99, 100.0, 98, 99, 10, 990, 1, 5, 99), cfg) is None
    assert try_fill(s, Bar(KEY, 0, 1, 99, 100.1, 98, 99, 10, 990, 1, 5, 99), cfg)[2] is True


def test_partial_fill_by_participation():
    cfg = FillModelConfig(participation=0.1)
    o = BtOrder(1, KEY, Side.BUY, 5.0, OrderType.MARKET, None, Urgency.HIGH, 0)
    bar = Bar(
        KEY,
        0,
        1,
        100,
        101,
        99,
        100,
        volume=10,
        quote_volume=1000,
        trade_count=1,
        taker_buy_volume=5,
        vwap=100,
    )
    qty, _, _ = try_fill(o, bar, cfg)
    assert qty == pytest.approx(1.0)


def test_slippage_model():
    cfg = FillModelConfig(participation=1.0, half_spread_bp=1.0, impact_k=0.1)
    o = BtOrder(1, KEY, Side.BUY, 1.0, OrderType.MARKET, None, Urgency.HIGH, 0)
    bar = Bar(
        KEY,
        0,
        1,
        100,
        101,
        99,
        100,
        volume=100,
        quote_volume=10_000,
        trade_count=1,
        taker_buy_volume=50,
        vwap=100,
    )
    _, price, _ = try_fill(o, bar, cfg)
    # notional 100 / 10000 = 0.01 → sqrt = 0.1 → impact 0.01 → +1bp スプレッド
    assert price == pytest.approx(100 * (1 + 0.0001 + 0.01))


def test_limit_escalates_to_market_when_unfilled():
    # 価格が上がり続けて買い指値が約定しない → escalate_bars 後に成行
    closes = [100 + i for i in range(12)]
    cfg = _cfg(escalate_bars=3, patience_bars=100)
    res = BacktestEngine(cfg, [Hold(1.0, Urgency.NORMAL)], {KEY: _bars(closes, gap=0.001)}).run()
    assert res.fills and not res.fills[0].is_maker
    assert res.fills[0].bars_to_fill == 4  # 3 バー未約定 → 4 バー目に成行


def test_low_urgency_gives_up():
    closes = [100 + i for i in range(12)]
    cfg = _cfg(giveup_bars=3, patience_bars=100)
    res = BacktestEngine(cfg, [Hold(1.0, Urgency.LOW)], {KEY: _bars(closes, gap=0.001)}).run()
    assert not res.fills and res.n_canceled >= 1


def test_funding_applied_with_sign():
    bars = _bars([100, 100, 100, 100])
    ts = bars["close_ts_ms"].to_list()
    funding = pl.DataFrame({"ts_ms": [ts[2]], "interval_hours": [1], "rate": [0.001]})
    res = BacktestEngine(_cfg(), [Hold(1.0)], {KEY: bars}, {KEY: funding}).run()
    # 10 枚ロング × 100 × 0.001 = 1.0 を支払う
    assert res.summary["funding_total"] == pytest.approx(-1.0)
    assert res.equity["equity"][-1] == pytest.approx(999.0)


def test_leverage_gate_caps_exposure():
    cfg = _cfg()
    cfg.max_leverage = 1.5

    class Greedy(Strategy):
        name = "greedy"

        def on_bar(self, ctx):
            return [
                Target(KEY, 1.0),
                Target(KEY, 1.0),
            ]  # 合計 2.0 → クリップ 1.0 → ゲート 1.5 は超えない

    res = BacktestEngine(cfg, [Greedy()], {KEY: _bars([100, 100, 100])}).run()
    assert res.equity["leverage"].max() <= 1.5 + 1e-9


def test_liquidation_is_detected():
    cfg = _cfg()
    cfg.max_leverage = 2.5
    cfg.max_symbol_exposure = 2.5
    cfg.maintenance_margin_rate = 0.05
    # 2.5 倍ロング。価格が 50% 下落 → 証拠金割れ
    res = BacktestEngine(cfg, [Hold(2.5)], {KEY: _bars([100, 100, 100, 50, 50])}).run()
    assert res.liquidated
    assert res.equity["equity"][-1] < 20


def test_drawdown_ladder():
    lad = DrawdownLadder()
    assert lad.update(0.1) == 1.0
    assert lad.update(0.2) == 0.5
    assert lad.update(0.1) == 0.5  # 戻っても自動では上げない
    assert lad.update(0.36) == 0.25
    assert lad.update(0.5) == 0.0 and lad.halted
    lad.reset()
    assert lad.scale == 1.0 and not lad.halted


def test_ladder_halts_engine_and_closes_position():
    # 段階的な下落では各段で規模を落とすので DD 50% に達しない（ラダーが機能している）
    closes = [100, 100, 80, 70, 60, 45, 30, 20, 20]
    res = BacktestEngine(_cfg(), [Hold(1.0)], {KEY: _bars(closes)}).run()
    assert 0.2 in res.ladder_triggered and 0.35 in res.ladder_triggered
    assert not res.halted
    assert res.equity["drawdown"].max() < 0.5
    # 一気の暴落（100 → 40）では DD 60% → 停止 → 成行で全決済
    res = BacktestEngine(_cfg(), [Hold(1.0)], {KEY: _bars([100, 100, 100, 40, 40])}).run()
    assert res.halted and 0.5 in res.ladder_triggered
    assert res.account.position(KEY).is_flat
