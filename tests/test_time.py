from datetime import UTC, datetime, timedelta

import pytest

from cryptobot.core.time import ensure_utc, floor_to, from_ms, interval_to_timedelta, to_ms


def test_naive_rejected():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2024, 1, 1))


def test_ms_roundtrip():
    dt = datetime(2024, 1, 1, 12, 34, 56, 789000, tzinfo=UTC)
    assert from_ms(to_ms(dt)) == dt


def test_floor():
    dt = datetime(2024, 1, 1, 12, 34, 56, tzinfo=UTC)
    assert floor_to(dt, timedelta(minutes=5)) == datetime(2024, 1, 1, 12, 30, tzinfo=UTC)


def test_interval():
    assert interval_to_timedelta("4h") == timedelta(hours=4)
    with pytest.raises(ValueError):
        interval_to_timedelta("7m")
