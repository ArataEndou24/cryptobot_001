import polars as pl

from cryptobot.data.quality import check_agg_trades, check_funding, check_klines
from tests.test_bars import _klines


def test_clean_klines_pass():
    rep = check_klines(_klines(), "1m")
    assert rep.ok, rep.to_text()


def test_missing_and_jump_detected():
    k = _klines(200)
    k = k.filter(pl.col("open_ts_ms") % 7 != 0)  # 適当に間引く → 欠損
    k = k.with_columns(
        pl.when(pl.arange(0, k.height) == 50)
        .then(pl.col("close") * 2)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    rep = check_klines(k, "1m", max_missing_ratio=0.0)
    names = {c.name: c.ok for c in rep.checks}
    assert not names["missing_bars"]
    assert not names["price_jumps"]


def test_agg_trades_gap():
    df = pl.DataFrame(
        {
            "agg_trade_id": [1, 2, 4],
            "price": [1.0, 1.0, 1.0],
            "qty": [1.0, 1.0, 1.0],
            "first_trade_id": [1, 2, 4],
            "last_trade_id": [1, 2, 4],
            "ts_ms": [1, 2, 3],
            "is_buyer_maker": [True, False, True],
        }
    )
    rep = check_agg_trades(df)
    assert not rep.ok
    assert any(c.name == "id_contiguous" and not c.ok for c in rep.checks)


def test_funding_extreme():
    df = pl.DataFrame({"ts_ms": [1, 2], "interval_hours": [8, 8], "rate": [0.0001, 0.05]})
    rep = check_funding(df)
    assert any(c.name == "rate_range" and not c.ok for c in rep.checks)
