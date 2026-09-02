"""重大度別のアラートルーティング（制約分析 4.2）。

- P0: 即時送信。同一キーの重複は dedup_window_sec 内で 1 回。
- P1: バッファし、flush() 時に 1 通へまとめる（呼び出し側が定期的に flush する）。
- P2: 日次要約用。flush_daily() で 1 通。
- 予算が閾値を下回ると P1/P2 を停止して P0 のみ送る。
- 送信手段（LINE）が失敗しても、必ずローカルログに残す。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

log = logging.getLogger(__name__)


class Severity(IntEnum):
    P0 = 0
    P1 = 1
    P2 = 2


@dataclass(frozen=True, slots=True)
class Alert:
    severity: Severity
    key: str  # 重複判定用（例: "dd_ladder:-20"）
    title: str
    body: str
    ts: float


@dataclass
class AlertRouter:
    send: Callable[[str, int], bool]  # (text, reserve_for_p0) -> ok
    remaining_budget: Callable[[], int]
    dedup_window_sec: float = 600.0
    p0_reserve: int = 30  # P1/P2 が使ってよいのは残りがこれを超える分のみ
    _last_sent: dict[str, float] = field(default_factory=dict)
    _p1_buffer: list[Alert] = field(default_factory=list)
    _p2_buffer: list[Alert] = field(default_factory=list)
    _clock: Callable[[], float] = time.time

    def emit(self, severity: Severity, key: str, title: str, body: str = "") -> None:
        alert = Alert(severity, key, title, body, self._clock())
        log.log(
            logging.CRITICAL if severity is Severity.P0 else logging.WARNING,
            "[%s] %s: %s",
            severity.name,
            title,
            body,
        )
        if severity is Severity.P0:
            last = self._last_sent.get(key)
            if last is not None and alert.ts - last < self.dedup_window_sec:
                return
            ok = self.send(self._format([alert]), 0)
            if ok:
                self._last_sent[key] = alert.ts
        elif severity is Severity.P1:
            self._p1_buffer.append(alert)
        else:
            self._p2_buffer.append(alert)

    def flush(self) -> None:
        """P1 バッファをまとめて 1 通にする。定期的（例: 5 分ごと）に呼ぶ。"""
        if not self._p1_buffer:
            return
        alerts, self._p1_buffer = self._p1_buffer, []
        if self.remaining_budget() <= self.p0_reserve:
            log.warning("予算温存のため P1 %d 件をスキップ", len(alerts))
            return
        self.send(self._format(alerts), self.p0_reserve)

    def flush_daily(self, header: str = "日次要約") -> None:
        alerts, self._p2_buffer = self._p2_buffer, []
        if self.remaining_budget() <= self.p0_reserve:
            log.warning("予算温存のため日次要約をスキップ")
            return
        text = header if not alerts else f"{header}\n" + self._format(alerts)
        self.send(text, self.p0_reserve)

    @staticmethod
    def _format(alerts: list[Alert]) -> str:
        parts = []
        for a in alerts:
            line = f"[{a.severity.name}] {a.title}"
            if a.body:
                line += f"\n{a.body}"
            parts.append(line)
        return "\n\n".join(parts)
