import pytest

from cryptobot.backtest.account import Account
from cryptobot.core.types import Side

FEE = 0.0005


def test_open_and_close_long_hand_calculated():
    a = Account(cash=700.0)
    a.apply_fill("ETH", Side.BUY, 0.1, 2000.0, FEE, 1)  # 手数料 0.1
    assert a.position("ETH").qty == pytest.approx(0.1)
    assert a.cash == pytest.approx(700 - 0.1)
    assert a.equity({"ETH": 2100.0}) == pytest.approx(699.9 + 10.0)
    r, f = a.apply_fill("ETH", Side.SELL, 0.1, 2100.0, FEE, 2)  # 実現 +10、手数料 0.105
    assert r == pytest.approx(10.0) and f == pytest.approx(0.105)
    assert a.position("ETH").is_flat
    assert a.cash == pytest.approx(700 - 0.1 + 10 - 0.105)
    t = a.totals()
    assert t["fee"] == pytest.approx(-0.205) and t["realized"] == pytest.approx(10.0)


def test_average_price_and_partial_close():
    a = Account(cash=1000.0)
    a.apply_fill("X", Side.BUY, 1.0, 100.0, 0.0, 1)
    a.apply_fill("X", Side.BUY, 1.0, 120.0, 0.0, 2)
    assert a.position("X").avg_price == pytest.approx(110.0)
    r, _ = a.apply_fill("X", Side.SELL, 0.5, 130.0, 0.0, 3)
    assert r == pytest.approx(10.0)
    assert a.position("X").qty == pytest.approx(1.5)
    assert a.position("X").avg_price == pytest.approx(110.0)


def test_flip_position():
    a = Account(cash=1000.0)
    a.apply_fill("X", Side.BUY, 1.0, 100.0, 0.0, 1)
    r, _ = a.apply_fill("X", Side.SELL, 3.0, 90.0, 0.0, 2)  # 1 枚決済 −10、2 枚ショート新規
    assert r == pytest.approx(-10.0)
    p = a.position("X")
    assert p.qty == pytest.approx(-2.0) and p.avg_price == pytest.approx(90.0)
    assert a.equity({"X": 80.0}) == pytest.approx(990 + 20)


def test_short_pnl_and_funding_sign():
    a = Account(cash=1000.0)
    a.apply_fill("X", Side.SELL, 2.0, 100.0, 0.0, 1)
    assert a.unrealized({"X": 90.0}) == pytest.approx(20.0)
    # 正の funding: ロングが払い、ショートが受け取る
    amt = a.apply_funding("X", 100.0, 0.0001, 2)
    assert amt == pytest.approx(0.02)
    a.apply_fill("X", Side.BUY, 2.0, 100.0, 0.0, 3)
    assert a.apply_funding("X", 100.0, 0.0001, 4) == 0.0  # フラットなら 0


def test_leverage_drawdown_and_liquidation():
    a = Account(cash=100.0)
    a.apply_fill("X", Side.BUY, 3.0, 100.0, 0.0, 1)  # 名目 300、レバ 3 倍
    marks = {"X": 100.0}
    assert a.leverage(marks) == pytest.approx(3.0)
    assert a.drawdown(marks) == 0.0
    marks = {"X": 80.0}  # 未実現 −60 → equity 40、DD 60%
    assert a.drawdown(marks) == pytest.approx(0.6)
    assert not a.is_liquidated(marks, {"X": 0.1})  # 必要証拠金 24 < 40
    marks = {"X": 70.0}  # equity 10 < 必要証拠金 21
    assert a.is_liquidated(marks, {"X": 0.1})
    a.liquidate_all(marks, fee_rate=0.01, ts_ms=5)
    assert a.position("X").is_flat
    assert a.cash == pytest.approx(100 - 90 - 2.1)
    assert a.totals()["liquidation_fee"] == pytest.approx(-2.1)


def test_equity_invariant_random_walk():
    import random

    rng = random.Random(0)
    a = Account(cash=10_000.0)
    price = 100.0
    for i in range(500):
        price *= 1 + rng.uniform(-0.01, 0.01)
        side = Side.BUY if rng.random() < 0.5 else Side.SELL
        a.apply_fill("X", side, rng.uniform(0.1, 2.0), price, FEE, i)
    # 全決済すると equity と cash が一致し、equity は決済前後で不変（手数料分だけ減る）
    p = a.position("X")
    eq_before = a.equity({"X": price})
    if not p.is_flat:
        a.apply_fill("X", Side.SELL if p.qty > 0 else Side.BUY, abs(p.qty), price, 0.0, 999)
    assert a.position("X").is_flat
    assert a.cash == pytest.approx(eq_before)
