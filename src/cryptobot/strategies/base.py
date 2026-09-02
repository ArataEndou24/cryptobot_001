"""戦略インターフェース。研究（バックテスト）と本番で同一のコードを呼ぶ。

戦略は「買え／売れ」ではなく「このエクスポージャーでいたい」を返す（設計書 8.1）。
- exposure: equity に対する名目の比率。+1 = 資産の 100% ロング、−0.5 = 50% ショート。
- urgency: 執行アルゴへの指示。LOW は約定しなければ諦める、HIGH は即成行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

import polars as pl

from cryptobot.core.types import Bar


class Urgency(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2


@dataclass(frozen=True, slots=True)
class Target:
    instrument_key: str
    exposure: float
    urgency: Urgency = Urgency.NORMAL


@dataclass(frozen=True, slots=True)
class BarContext:
    """戦略に渡す「時点 ts_ms で知り得る情報」。

    history(key, n) は確定済みバー（close_ts_ms <= ts_ms）の直近 n 本だけを返す。
    それ以外の経路でデータに触れる戦略は先読みの疑いがあるので禁止。
    """

    ts_ms: int
    bars: dict[str, Bar]
    history: Callable[[str, int], pl.DataFrame]
    equity: float
    positions: dict[str, float]  # key → 符号付き数量


class Strategy(ABC):
    name: str = "unnamed"

    @abstractmethod
    def on_bar(self, ctx: BarContext) -> list[Target]: ...

    def warmup_bars(self) -> int:
        """この本数が揃うまで on_bar を呼ばない。"""
        return 0
