import numpy as np
import polars as pl

from cryptobot.data.bars import dollar_bars, klines_to_bars, resample_time_bars
from cryptobot.data.store import BAR_SCHEMA, KLINE_SCHEMA


def _klines(n: int = 120, start_ms: int = 1_699_999_200_000) -> pl.DataFrame:
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.1, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) + 0.05
    low = np.minimum(open_, close) - 0.05
    vol = rng.uniform(1, 10, n)
    tb = vol * rng.uniform(0.3, 0.7, n)
    ts = start_ms + np.arange(n) * 60_000
    return pl.DataFrame(
        {
            "open_ts_ms": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "close_ts_ms": ts + 59_999,
            "quote_volume": vol * close,
            "trade_count": rng.integers(1, 50, n),
            "taker_buy_volume": tb,
            "taker_buy_quote_volume": tb * close,
        },
        schema=KLINE_SCHEMA,
    )


def test_klines_to_bars_schema_and_close_ts():
    b = klines_to_bars(_klines())
    assert b.schema == pl.Schema(BAR_SCHEMA)
    assert (b["close_ts_ms"] - b["open_ts_ms"] == 60_000).all()


def test_resample_5m_aggregates_correctly():
    k = _klines(120)
    b1 = klines_to_bars(k)
    b5 = resample_time_bars(b1, "5m")
    assert b5.height == 24
    first = b1.head(5)
    row = b5.row(0, named=True)
    assert row["open"] == first["open"][0]
    assert row["close"] == first["close"][-1]
    assert row["high"] == first["high"].max()
    assert row["low"] == first["low"].min()
    assert abs(row["volume"] - first["volume"].sum()) < 1e-9
    assert row["close_ts_ms"] == row["open_ts_ms"] + 5 * 60_000
    # 各バーの vwap は quote/volume
    assert abs(row["vwap"] - first["quote_volume"].sum() / first["volume"].sum()) < 1e-9


def test_resample_preserves_totals():
    b1 = klines_to_bars(_klines(240))
    b1h = resample_time_bars(b1, "1h")
    assert b1h.height == 4
    assert abs(b1h["volume"].sum() - b1["volume"].sum()) < 1e-9
    assert b1h["trade_count"].sum() == b1["trade_count"].sum()


def test_dollar_bars():
    n = 1000
    rng = np.random.default_rng(1)
    price = 100 + np.cumsum(rng.normal(0, 0.01, n))
    qty = rng.uniform(0.1, 1.0, n)
    df = pl.DataFrame(
        {
            "agg_trade_id": np.arange(n),
            "price": price,
            "qty": qty,
            "first_trade_id": np.arange(n) * 2,
            "last_trade_id": np.arange(n) * 2 + 1,
            "ts_ms": 1_700_000_000_000 + np.arange(n) * 100,
            "is_buyer_maker": rng.random(n) < 0.5,
        }
    )
    total_quote = float((price * qty).sum())
    bars = dollar_bars(df, threshold_quote=1000.0)
    assert bars.schema == pl.Schema(BAR_SCHEMA)
    assert abs(bars["quote_volume"].sum() - total_quote) < 1e-6
    assert bars["trade_count"].sum() == 2 * n
    # 最後のバー以外はおおむね閾値の出来高
    inner = bars["quote_volume"][:-1]
    assert inner.min() > 900 and inner.max() < 1200
    assert (bars["taker_buy_volume"] <= bars["volume"] + 1e-9).all()
