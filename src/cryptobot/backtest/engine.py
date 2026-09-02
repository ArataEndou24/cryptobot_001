"""イベント駆動バックテスター（Bar モード）。仕様: docs/03_BACKTESTER.md。

イベント順序（同一時刻）: 未約定注文の約定判定 → 資金調達 → 清算判定 → 時価評価 → 戦略 → 発注。
発注は「このバーの close で行い、次バー以降に市場へ届く」。同じバーで約定させない。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import polars as pl

from cryptobot.backtest.account import Account
from cryptobot.backtest.metrics import equity_metrics, fill_metrics
from cryptobot.backtest.orders import BtFill, BtOrder, FillModelConfig, try_fill
from cryptobot.core.types import Bar, InstrumentSpec, OrderType, Side
from cryptobot.risk.ladder import DrawdownLadder
from cryptobot.strategies.base import BarContext, Strategy, Target, Urgency

log = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    initial_cash: float
    specs: dict[str, InstrumentSpec]
    fill: FillModelConfig = field(default_factory=FillModelConfig)
    maintenance_margin_rate: float = 0.02
    liquidation_fee: float = 0.01
    max_leverage: float = 2.5  # 総レバレッジ上限（リスクゲート）
    max_symbol_exposure: float = 1.0  # 1 銘柄の |exposure| 上限（D-10: 小資金期は 100%）
    ladder_steps: list[tuple[float, float]] | None = None


@dataclass
class BacktestResult:
    fills: list[BtFill]
    equity: pl.DataFrame  # ts_ms, cash, equity, gross_notional, leverage, drawdown, scale
    summary: dict[str, float]
    liquidated: bool
    halted: bool
    ladder_triggered: list[float]
    n_orders: int
    n_canceled: int
    account: Account


def _row_to_bar(key: str, r: dict) -> Bar:  # type: ignore[type-arg]
    return Bar(
        instrument_key=key,
        open_ts_ms=r["open_ts_ms"],
        close_ts_ms=r["close_ts_ms"],
        open=r["open"],
        high=r["high"],
        low=r["low"],
        close=r["close"],
        volume=r["volume"],
        quote_volume=r["quote_volume"],
        trade_count=r["trade_count"],
        taker_buy_volume=r["taker_buy_volume"],
        vwap=r["vwap"],
    )


class BacktestEngine:
    def __init__(
        self,
        cfg: BacktestConfig,
        strategies: list[Strategy],
        bars: dict[str, pl.DataFrame],
        funding: dict[str, pl.DataFrame] | None = None,
    ):
        self.cfg = cfg
        self.strategies = strategies
        self.bars = {k: v.sort("close_ts_ms") for k, v in bars.items()}
        self.funding = {k: v.sort("ts_ms") for k, v in (funding or {}).items()}
        self.account = Account(cash=cfg.initial_cash)
        self.ladder = DrawdownLadder(cfg.ladder_steps) if cfg.ladder_steps else DrawdownLadder()
        self.open_orders: list[BtOrder] = []
        self.fills: list[BtFill] = []
        self.equity_rows: list[dict[str, float]] = []
        self._next_order_id = 1
        self._n_orders = 0
        self._n_canceled = 0
        self._closed_idx: dict[str, int] = {k: -1 for k in self.bars}  # 直近確定バーの行番号
        self._last_close: dict[str, float] = {}
        self._funding_ptr: dict[str, int] = {k: 0 for k in self.funding}
        self.liquidated = False
        self.warmup = max((s.warmup_bars() for s in strategies), default=0)

    # ---- 履歴アクセス（先読み防止: 確定済みバーのみ） ----
    def _history(self, key: str, n: int) -> pl.DataFrame:
        end = self._closed_idx[key] + 1
        return self.bars[key].slice(max(0, end - n), min(n, end))

    # ---- メインループ ----
    def run(self) -> BacktestResult:
        grid = sorted({int(t) for df in self.bars.values() for t in df["close_ts_ms"].to_list()})
        # 各銘柄の close_ts → 行番号
        row_by_ts: dict[str, dict[int, int]] = {
            k: {int(t): i for i, t in enumerate(df["close_ts_ms"].to_list())}
            for k, df in self.bars.items()
        }
        rows_cache: dict[str, list[dict]] = {k: df.to_dicts() for k, df in self.bars.items()}  # type: ignore[type-arg]
        prev_ts = grid[0] - 1 if grid else 0
        for bar_idx, ts in enumerate(grid):
            bars_now: dict[str, Bar] = {}
            for key in self.bars:
                i = row_by_ts[key].get(ts)
                if i is None:
                    continue
                bar = _row_to_bar(key, rows_cache[key][i])
                bars_now[key] = bar
                self._closed_idx[key] = i
                self._last_close[key] = bar.close

            # 1. 約定判定（前のバーで出した注文のみ）
            for bar in bars_now.values():
                self._process_orders(bar, bar_idx)

            # 2. 資金調達
            self._apply_funding(prev_ts, ts)

            # 3. 清算判定（バー内の最悪価格）
            if not self.liquidated:
                self._check_liquidation(bars_now, ts)

            # 4. 時価評価・ラダー
            marks = dict(self._last_close)
            equity = self.account.equity(marks)
            dd = self.account.drawdown(marks)
            scale = self.ladder.update(dd)
            self.equity_rows.append(
                {
                    "ts_ms": ts,
                    "cash": self.account.cash,
                    "equity": equity,
                    "gross_notional": self.account.gross_notional(marks),
                    "leverage": self.account.leverage(marks) if equity > 0 else 0.0,
                    "drawdown": dd,
                    "scale": scale,
                }
            )

            # 5. 戦略
            if bars_now and not self.liquidated:
                if self.ladder.halted:
                    targets = [Target(k, 0.0, Urgency.HIGH) for k in self._last_close]
                elif bar_idx >= self.warmup:
                    targets = self._collect_targets(ts, bars_now, equity, scale)
                else:
                    targets = []
                # 6. 発注
                for t in targets:
                    if t.instrument_key in self._last_close:
                        self._plan_order(t, equity, bar_idx)
            prev_ts = ts

        equity_df = (
            pl.DataFrame(self.equity_rows)
            if self.equity_rows
            else pl.DataFrame(schema={"ts_ms": pl.Int64, "equity": pl.Float64})
        )
        summary = {**equity_metrics(equity_df), **fill_metrics(self.fills)}
        totals = self.account.totals()
        summary["funding_total"] = totals.get("funding", 0.0)
        summary["fee_total"] = totals.get("fee", 0.0) + totals.get("liquidation_fee", 0.0)
        summary["liquidated"] = float(self.liquidated)
        return BacktestResult(
            fills=self.fills,
            equity=equity_df,
            summary=summary,
            liquidated=self.liquidated,
            halted=self.ladder.halted,
            ladder_triggered=list(self.ladder.triggered),
            n_orders=self._n_orders,
            n_canceled=self._n_canceled,
            account=self.account,
        )

    # ---- 注文処理 ----
    def _process_orders(self, bar: Bar, bar_idx: int) -> None:
        cfg = self.cfg.fill
        spec = self.cfg.specs[bar.instrument_key]
        remaining: list[BtOrder] = []
        for o in self.open_orders:
            if o.instrument_key != bar.instrument_key:
                remaining.append(o)
                continue
            if o.created_bar >= bar_idx:  # 同じバーでは約定させない
                remaining.append(o)
                continue
            o.bars_open += 1
            res = try_fill(o, bar, cfg)
            if res is not None:
                qty, price, is_maker = res
                fee_rate = float(spec.maker_fee if is_maker else spec.taker_fee)
                _, fee = self.account.apply_fill(
                    o.instrument_key, o.side, qty, price, fee_rate, bar.close_ts_ms, note=o.strategy
                )
                self.fills.append(
                    BtFill(
                        o.order_id,
                        o.instrument_key,
                        o.strategy,
                        o.side,
                        qty,
                        price,
                        is_maker,
                        fee,
                        bar.close_ts_ms,
                        o.ref_price,
                        o.bars_open,
                    )
                )
                o.qty -= qty
                o.filled_qty += qty
                if o.qty * bar.close < float(spec.min_notional) * 0.5:
                    continue  # 実質全約定
            # 未約定（または残あり）: 追従・エスカレーション・諦め
            if o.order_type is OrderType.LIMIT:
                if o.urgency is Urgency.LOW and o.bars_open >= cfg.giveup_bars:
                    self._n_canceled += 1
                    continue
                if o.urgency is Urgency.NORMAL and o.bars_open >= cfg.escalate_bars:
                    o.order_type = OrderType.MARKET
                    o.limit_price = None
                    o.events.append(f"escalate@{bar_idx}")
                elif o.bars_open % cfg.patience_bars == 0:
                    o.limit_price = float(spec.round_price(bar.close))
                    o.reprices += 1
            remaining.append(o)
        self.open_orders = remaining

    def _apply_funding(self, prev_ts: int, ts: int) -> None:
        for key, df in self.funding.items():
            ptr = self._funding_ptr[key]
            ts_col, rate_col = df["ts_ms"], df["rate"]
            n = df.height
            while ptr < n and ts_col[ptr] <= ts:
                if ts_col[ptr] > prev_ts and key in self._last_close:
                    self.account.apply_funding(
                        key, self._last_close[key], rate_col[ptr], ts_col[ptr]
                    )
                ptr += 1
            self._funding_ptr[key] = ptr

    def _check_liquidation(self, bars_now: dict[str, Bar], ts: int) -> None:
        worst = dict(self._last_close)
        for key, p in self.account.positions.items():
            if p.is_flat or key not in bars_now:
                continue
            worst[key] = bars_now[key].low if p.qty > 0 else bars_now[key].high
        mmr = {k: self.cfg.maintenance_margin_rate for k in worst}
        if self.account.is_liquidated(worst, mmr):
            log.error("清算発生 ts=%d equity=%.2f", ts, self.account.equity(worst))
            self.account.liquidate_all(worst, self.cfg.liquidation_fee, ts)
            self.liquidated = True
            self._n_canceled += len(self.open_orders)
            self.open_orders = []

    def _collect_targets(
        self, ts: int, bars_now: dict[str, Bar], equity: float, scale: float
    ) -> list[Target]:
        ctx = BarContext(
            ts_ms=ts,
            bars=bars_now,
            history=self._history,
            equity=equity,
            positions={k: p.qty for k, p in self.account.positions.items() if not p.is_flat},
        )
        agg: dict[str, float] = {}
        urg: dict[str, Urgency] = {}
        for s in self.strategies:
            for t in s.on_bar(ctx):
                agg[t.instrument_key] = agg.get(t.instrument_key, 0.0) + t.exposure
                urg[t.instrument_key] = max(urg.get(t.instrument_key, Urgency.LOW), t.urgency)
        cap = self.cfg.max_symbol_exposure
        return [Target(k, max(-cap, min(cap, v)) * scale, urg[k]) for k, v in agg.items()]

    def _pending_qty(self, key: str) -> float:
        return sum(o.side.sign * o.qty for o in self.open_orders if o.instrument_key == key)

    def _plan_order(self, t: Target, equity: float, bar_idx: int) -> None:
        key = t.instrument_key
        spec = self.cfg.specs[key]
        price = self._last_close[key]
        current = self.account.position(key).qty
        desired = t.exposure * equity / price
        gap = desired - current  # 未約定注文を無視した「本当に動かしたい量」
        if abs(gap) * price < float(spec.min_notional):
            return
        side = Side.BUY if gap > 0 else Side.SELL
        # 方向が変わった: 反対方向の未約定注文を取り消す
        before = len(self.open_orders)
        self.open_orders = [
            o for o in self.open_orders if not (o.instrument_key == key and o.side is not side)
        ]
        self._n_canceled += before - len(self.open_orders)
        # 同方向の未約定分を差し引く。過剰に出ている場合は何もしない（指値は追従で調整される）
        delta = gap - self._pending_qty(key)
        if delta * gap <= 0:
            return
        qty = float(spec.round_qty(abs(delta)))
        if qty * price < float(spec.min_notional):
            return
        # リスクゲート: 建て増しでレバレッジ上限を超える分は削る
        reduces = (current > 0 and side is Side.SELL) or (current < 0 and side is Side.BUY)
        if not reduces:
            marks = dict(self._last_close)
            room = self.cfg.max_leverage * equity - self.account.gross_notional(marks)
            room -= sum(o.qty * self._last_close[o.instrument_key] for o in self.open_orders)
            if room <= 0:
                return
            qty = min(qty, float(spec.round_qty(room / price)))
            if qty * price < float(spec.min_notional):
                return
        if t.urgency is Urgency.HIGH:
            otype, lp = OrderType.MARKET, None
        else:
            otype, lp = OrderType.LIMIT, float(spec.round_price(price))
        self.open_orders.append(
            BtOrder(
                self._next_order_id,
                key,
                side,
                qty,
                otype,
                lp,
                t.urgency,
                bar_idx,
                strategy="+".join(s.name for s in self.strategies),
                is_reduce_only=reduces,
                ref_price=price,
            )
        )
        self._next_order_id += 1
        self._n_orders += 1
