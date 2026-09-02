"""保存済み 1 分足からバーを生成し、品質レポートを出す。

例:
  uv run python scripts/build_bars.py --symbols BTCUSDT ETHUSDT \
      --intervals 5m 15m 1h 4h --dollar 1e6
"""

from __future__ import annotations

import argparse
import logging

from cryptobot.core.config import load_settings
from cryptobot.data.bars import dollar_bars, klines_to_bars, resample_time_bars
from cryptobot.data.binance_vision import VENUE
from cryptobot.data.quality import check_agg_trades, check_funding, check_klines
from cryptobot.data.store import DataStore

log = logging.getLogger("build_bars")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--intervals", nargs="*", default=["5m", "15m", "1h", "4h", "1d"])
    ap.add_argument(
        "--dollar", type=float, default=None, help="ドルバー閾値（quote 建て）。省略で作らない"
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    store = DataStore(load_settings().data_dir)
    for sym in args.symbols:
        klines = store.read_raw(VENUE, sym, "klines_1m")
        print(check_klines(klines, "1m", subject=f"{sym} klines_1m").to_text())
        if klines.is_empty():
            continue
        bars_1m = klines_to_bars(klines)
        store.write_bars(VENUE, sym, "time_1m", "all", bars_1m)
        for itv in args.intervals:
            b = resample_time_bars(bars_1m, itv)
            store.write_bars(VENUE, sym, f"time_{itv}", "all", b)
            log.info("%s time_%s bars=%d", sym, itv, b.height)

        funding = store.read_raw(VENUE, sym, "funding")
        if not funding.is_empty():
            print(check_funding(funding, subject=f"{sym} funding").to_text())

        if args.dollar:
            parts = store.list_raw_partitions(VENUE, sym, "agg_trades")
            total = 0
            for p in parts:
                trades = store.read_raw(VENUE, sym, "agg_trades", [p])
                rep = check_agg_trades(trades, subject=f"{sym} agg_trades {p}")
                if not rep.ok:
                    print(rep.to_text())
                b = dollar_bars(trades, args.dollar)
                store.write_bars(VENUE, sym, f"dollar_{args.dollar:g}", p, b)
                total += b.height
            log.info("%s dollar bars=%d over %d days", sym, total, len(parts))


if __name__ == "__main__":
    main()
