"""Hyperliquid アダプタ（第 1 系統）。

このモジュールは **認証不要の公開 info API** のみを扱う（仕様取得・直近の足・資金調達率履歴）。
発注（TradingVenue）は本番フェーズで別モジュールとして実装する（署名が必要）。

仕様の要点（公式ドキュメントに基づく。変更されうるので `refresh_specs` で定期更新する）:
- 数量の小数桁は `szDecimals`。価格は有効数字 5 桁以内かつ小数桁 ≤ (6 − szDecimals)。
- 最小注文額は 10 USD（名目）。
- 手数料はティア制。基本ティアは taker 0.045% / maker 0.015%（既定値。設定で上書き可）。
- 資金調達は 1 時間ごと。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx
import polars as pl

from cryptobot.core.types import Instrument, InstrumentSpec, InstrumentType
from cryptobot.data.store import FUNDING_SCHEMA, KLINE_SCHEMA, enforce_schema

log = logging.getLogger(__name__)

VENUE = "hyperliquid"
MAINNET_INFO_URL = "https://api.hyperliquid.xyz/info"
TESTNET_INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
MIN_NOTIONAL_USD = Decimal("10")
DEFAULT_MAKER_FEE = Decimal("0.00015")
DEFAULT_TAKER_FEE = Decimal("0.00045")
FUNDING_INTERVAL_HOURS = 1
MAX_PRICE_SIG_FIGS = 5
MAX_PRICE_DECIMALS_PERP = 6


@dataclass(frozen=True, slots=True)
class AssetContext:
    """metaAndAssetCtxs の 1 銘柄分。ユニバース選定に使う。"""

    name: str
    sz_decimals: int
    max_leverage: int
    mark_px: float
    day_notional_volume: float
    open_interest_base: float
    funding_hourly: float
    is_delisted: bool

    @property
    def open_interest_usd(self) -> float:
        return self.open_interest_base * self.mark_px

    @property
    def lot_notional_usd(self) -> float:
        """数量 1 刻みの名目額。"""
        return 10 ** (-self.sz_decimals) * self.mark_px

    @property
    def min_order_usd(self) -> float:
        return max(float(MIN_NOTIONAL_USD), self.lot_notional_usd)

    def sizing_steps(self, capital_usd: float) -> float:
        """資金 1 倍分のエクスポージャーを何段階で刻めるか。"""
        return capital_usd / self.min_order_usd


def price_tick(mark_px: float, sz_decimals: int) -> Decimal:
    """Hyperliquid の価格刻み: 有効数字 5 桁 かつ 小数桁 ≤ 6 − szDecimals。

    刻みは価格帯によって変わるため、現在の価格から決める。
    """
    max_decimals = MAX_PRICE_DECIMALS_PERP - sz_decimals
    px = Decimal(str(mark_px))
    int_digits = len(str(int(px))) if px >= 1 else 0
    if px >= 1:
        decimals_by_sig = max(0, MAX_PRICE_SIG_FIGS - int_digits)
    else:
        # 1 未満: 先頭のゼロを除いて有効数字 5 桁
        s = format(px, "f").split(".")[1]
        leading_zeros = len(s) - len(s.lstrip("0"))
        decimals_by_sig = leading_zeros + MAX_PRICE_SIG_FIGS
    decimals = min(max_decimals, decimals_by_sig)
    return Decimal(1).scaleb(-decimals)


def instrument_for(name: str) -> Instrument:
    return Instrument(VENUE, name, name, "USDC", InstrumentType.PERPETUAL)


class HyperliquidInfo:
    def __init__(
        self,
        client: httpx.Client | None = None,
        testnet: bool = False,
        maker_fee: Decimal = DEFAULT_MAKER_FEE,
        taker_fee: Decimal = DEFAULT_TAKER_FEE,
    ):
        self.url = TESTNET_INFO_URL if testnet else MAINNET_INFO_URL
        self.client = client or httpx.Client(timeout=30.0)
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

    def _post(self, body: dict) -> object:  # type: ignore[type-arg]
        r = self.client.post(self.url, json=body)
        r.raise_for_status()
        return r.json()

    # ---- 仕様・ユニバース ----
    def asset_contexts(self) -> list[AssetContext]:
        meta, ctxs = self._post({"type": "metaAndAssetCtxs"})  # type: ignore[misc]
        out = []
        for a, c in zip(meta["universe"], ctxs, strict=True):
            out.append(
                AssetContext(
                    name=a["name"],
                    sz_decimals=int(a["szDecimals"]),
                    max_leverage=int(a["maxLeverage"]),
                    mark_px=float(c["markPx"]),
                    day_notional_volume=float(c["dayNtlVlm"]),
                    open_interest_base=float(c["openInterest"]),
                    funding_hourly=float(c["funding"]),
                    is_delisted=bool(a.get("isDelisted", False)),
                )
            )
        return out

    def spec_from_context(self, ctx: AssetContext) -> InstrumentSpec:
        step = Decimal(1).scaleb(-ctx.sz_decimals)
        return InstrumentSpec(
            instrument=instrument_for(ctx.name),
            tick_size=price_tick(ctx.mark_px, ctx.sz_decimals),
            step_size=step,
            min_qty=step,
            min_notional=MIN_NOTIONAL_USD,
            max_leverage=ctx.max_leverage,
            maker_fee=self.maker_fee,
            taker_fee=self.taker_fee,
        )

    def specs(self) -> dict[str, InstrumentSpec]:
        return {
            c.name: self.spec_from_context(c) for c in self.asset_contexts() if not c.is_delisted
        }

    def select_universe(
        self,
        capital_usd: float,
        min_day_volume_usd: float = 300e6,
        min_steps: float = 20.0,
    ) -> list[AssetContext]:
        """流動性とサイズ刻みの条件を満たす銘柄を出来高順に返す。"""
        ctxs = [
            c
            for c in self.asset_contexts()
            if not c.is_delisted
            and c.day_notional_volume >= min_day_volume_usd
            and c.sizing_steps(capital_usd) >= min_steps
        ]
        return sorted(ctxs, key=lambda c: -c.day_notional_volume)

    # ---- 直近データ（照合・キャリブレーション用。長期履歴は Binance を使う） ----
    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> pl.DataFrame:
        """足。API は 1 回あたり最大 5000 本程度。

        KLINE_SCHEMA に揃える（taker 内訳は提供されないので 0）。
        """
        data = self._post(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            }
        )
        if not data:
            return pl.DataFrame(schema=KLINE_SCHEMA)
        df = pl.DataFrame(data)  # t, T, s, i, o, c, h, l, v, n
        df = df.select(
            pl.col("t").cast(pl.Int64).alias("open_ts_ms"),
            pl.col("o").cast(pl.Float64).alias("open"),
            pl.col("h").cast(pl.Float64).alias("high"),
            pl.col("l").cast(pl.Float64).alias("low"),
            pl.col("c").cast(pl.Float64).alias("close"),
            pl.col("v").cast(pl.Float64).alias("volume"),
            pl.col("T").cast(pl.Int64).alias("close_ts_ms"),
            (pl.col("v").cast(pl.Float64) * pl.col("c").cast(pl.Float64)).alias("quote_volume"),
            pl.col("n").cast(pl.Int64).alias("trade_count"),
            pl.lit(0.0).alias("taker_buy_volume"),
            pl.lit(0.0).alias("taker_buy_quote_volume"),
        )
        return enforce_schema(df, KLINE_SCHEMA)

    def funding_history(self, coin: str, start_ms: int, end_ms: int | None = None) -> pl.DataFrame:
        body: dict = {"type": "fundingHistory", "coin": coin, "startTime": start_ms}  # type: ignore[type-arg]
        if end_ms is not None:
            body["endTime"] = end_ms
        data = self._post(body)
        if not data:
            return pl.DataFrame(schema=FUNDING_SCHEMA)
        df = pl.DataFrame(data).select(
            pl.col("time").cast(pl.Int64).alias("ts_ms"),
            pl.lit(FUNDING_INTERVAL_HOURS).cast(pl.Int64).alias("interval_hours"),
            pl.col("fundingRate").cast(pl.Float64).alias("rate"),
        )
        return enforce_schema(df, FUNDING_SCHEMA)
