"""時刻ユーティリティ。

本プロジェクトの時刻に関する規約:
- 内部表現はすべて **UTC の timezone-aware datetime** か **ミリ秒 epoch (int)**。
  naive datetime は禁止。
- 取引所から受け取ったタイムスタンプ（イベント時刻）と、ローカル受信時刻を区別して保持する。
- 「時点 t で知り得た情報」の境界は `as_of` で表し、
  特徴量計算は必ず `as_of` 以前のデータのみを使う。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def ensure_utc(dt: datetime) -> datetime:
    """naive datetime を拒否し、aware なら UTC に正規化する。"""
    if dt.tzinfo is None:
        raise ValueError("naive datetime は禁止。timezone-aware (UTC) を渡すこと")
    return dt.astimezone(UTC)


def to_ms(dt: datetime) -> int:
    return int(ensure_utc(dt).timestamp() * 1000)


def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def floor_to(dt: datetime, step: timedelta) -> datetime:
    """dt を step 境界（epoch 起点）に切り下げる。"""
    dt = ensure_utc(dt)
    ms = to_ms(dt)
    step_ms = int(step.total_seconds() * 1000)
    return from_ms(ms - (ms % step_ms))


def date_range(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    """[start, end) を step 刻みで列挙する。"""
    start, end = ensure_utc(start), ensure_utc(end)
    out: list[datetime] = []
    cur = start
    while cur < end:
        out.append(cur)
        cur += step
    return out


INTERVAL_TO_TIMEDELTA: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
}


def interval_to_timedelta(interval: str) -> timedelta:
    try:
        return INTERVAL_TO_TIMEDELTA[interval]
    except KeyError as e:
        raise ValueError(f"未対応の足種: {interval}") from e
