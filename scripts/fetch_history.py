"""Binance 公開データから履歴を取得して保存する。

例:
  uv run python scripts/fetch_history.py --symbols BTCUSDT ETHUSDT \
      --start 2024-01-01 --end 2024-01-08 --klines --funding --agg-trades --metrics
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from cryptobot.core.config import load_settings
from cryptobot.data.binance_vision import VENUE, BinanceVisionSource
from cryptobot.data.store import DataStore

log = logging.getLogger("fetch_history")


def month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument(
        "--end", type=date.fromisoformat, required=True, help="排他的（この日は含まない）"
    )
    ap.add_argument("--klines", action="store_true")
    ap.add_argument("--agg-trades", action="store_true")
    ap.add_argument("--funding", action="store_true")
    ap.add_argument("--metrics", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = load_settings()
    store = DataStore(settings.data_dir)
    src = BinanceVisionSource()

    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days)]
    for sym in args.symbols:
        for d in days:
            part = store.day_partition(d)
            if args.klines:
                if args.overwrite or not store.has_raw(VENUE, sym, "klines_1m", part):
                    df = src.fetch_klines(sym, "1m", d)
                    if df is None:
                        log.warning("klines なし %s %s", sym, d)
                    else:
                        store.write_raw(VENUE, sym, "klines_1m", part, df, overwrite=args.overwrite)
                        log.info("klines %s %s rows=%d", sym, d, df.height)
            if args.agg_trades:
                if args.overwrite or not store.has_raw(VENUE, sym, "agg_trades", part):
                    df = src.fetch_agg_trades(sym, d)
                    if df is None:
                        log.warning("aggTrades なし %s %s", sym, d)
                    else:
                        store.write_raw(
                            VENUE, sym, "agg_trades", part, df, overwrite=args.overwrite
                        )
                        log.info("aggTrades %s %s rows=%d", sym, d, df.height)
            if args.metrics:
                if args.overwrite or not store.has_raw(VENUE, sym, "metrics", part):
                    df = src.fetch_metrics(sym, d)
                    if df is None:
                        log.warning("metrics なし %s %s", sym, d)
                    else:
                        store.write_raw(VENUE, sym, "metrics", part, df, overwrite=args.overwrite)
                        log.info("metrics %s %s rows=%d", sym, d, df.height)
        if args.funding:
            for y, m in month_iter(args.start, args.end - timedelta(days=1)):
                part = store.month_partition(y, m)
                if args.overwrite or not store.has_raw(VENUE, sym, "funding", part):
                    df = src.fetch_funding(sym, y, m)
                    if df is None:
                        log.warning("funding なし %s %s", sym, part)
                    else:
                        store.write_raw(VENUE, sym, "funding", part, df, overwrite=args.overwrite)
                        log.info("funding %s %s rows=%d", sym, part, df.height)


if __name__ == "__main__":
    main()
