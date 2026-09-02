from decimal import Decimal

from cryptobot.core.types import Bar, Instrument, InstrumentSpec, InstrumentType, OrderStatus, Side


def _spec() -> InstrumentSpec:
    inst = Instrument("binance_um", "ETHUSDT", "ETH", "USDT", InstrumentType.PERPETUAL)
    return InstrumentSpec(
        instrument=inst,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("20"),
        max_leverage=50,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
    )


def test_side():
    assert Side.BUY.sign == 1 and Side.SELL.sign == -1
    assert Side.BUY.opposite() is Side.SELL


def test_spec_rounding_is_down():
    s = _spec()
    assert s.round_price(1234.567) == Decimal("1234.56")
    assert s.round_qty(0.12349) == Decimal("0.123")


def test_spec_validation_small_capital():
    s = _spec()
    ok, _ = s.is_valid_order(Decimal("0.001"), Decimal("3000"))
    assert not ok  # 名目 3 USDT < 20
    ok, _ = s.is_valid_order(Decimal("0.01"), Decimal("3000"))
    assert ok


def test_order_status_terminal():
    assert OrderStatus.FILLED.is_terminal
    assert not OrderStatus.UNKNOWN.is_terminal


def test_bar_imbalance():
    b = Bar(
        "k",
        0,
        60_000,
        1,
        2,
        0.5,
        1.5,
        volume=10,
        quote_volume=12,
        trade_count=3,
        taker_buy_volume=7.5,
        vwap=1.2,
    )
    assert b.taker_sell_volume == 2.5
    assert abs(b.taker_imbalance - 0.5) < 1e-12
