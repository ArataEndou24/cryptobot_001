"""成績指標（仕様 8 章）。equity 曲線と約定リストから計算する。"""

from __future__ import annotations

import math

import polars as pl

from cryptobot.backtest.orders import BtFill

MS_PER_YEAR = 365.25 * 24 * 3600 * 1000


def equity_metrics(equity: pl.DataFrame) -> dict[str, float]:
    """equity: 列 ts_ms, equity。"""
    if equity.height < 2:
        return {"total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "calmar": 0.0}
    eq = equity["equity"]
    ts = equity["ts_ms"]
    rets = (eq / eq.shift(1) - 1).drop_nulls()
    span_years = (ts[-1] - ts[0]) / MS_PER_YEAR
    bars_per_year = rets.len() / span_years if span_years > 0 else 0.0
    mean, std = rets.mean() or 0.0, rets.std() or 0.0
    sharpe = (mean / std) * math.sqrt(bars_per_year) if std > 0 and bars_per_year > 0 else 0.0
    peak = eq.cum_max()
    dd = (1 - eq / peak).max() or 0.0
    total = eq[-1] / eq[0] - 1
    # 年率換算は 1 週間未満の期間では意味がない（数値も爆発する）ので 0 とする
    ann_ret = 0.0
    if span_years >= 7 / 365.25 and eq[0] > 0 and 1 + total > 0:
        try:
            ann_ret = (1 + total) ** (1 / span_years) - 1
        except OverflowError:
            ann_ret = float("inf")
    calmar = ann_ret / dd if dd > 0 else 0.0
    return {
        "total_return": float(total),
        "annual_return": float(ann_ret),
        "sharpe": float(sharpe),
        "max_drawdown": float(dd),
        "calmar": float(calmar),
        "span_years": float(span_years),
    }


def fill_metrics(fills: list[BtFill]) -> dict[str, float]:
    if not fills:
        return {
            "n_fills": 0,
            "fees": 0.0,
            "maker_ratio": 0.0,
            "avg_slippage_bp": 0.0,
            "turnover": 0.0,
        }
    notional = sum(f.qty * f.price for f in fills)
    maker = sum(f.qty * f.price for f in fills if f.is_maker)
    return {
        "n_fills": len(fills),
        "fees": sum(f.fee for f in fills),
        "maker_ratio": maker / notional if notional else 0.0,
        "avg_slippage_bp": sum(f.slippage_bp * f.qty * f.price for f in fills) / notional,
        "turnover": notional,
        "avg_bars_to_fill": sum(f.bars_to_fill for f in fills) / len(fills),
    }
