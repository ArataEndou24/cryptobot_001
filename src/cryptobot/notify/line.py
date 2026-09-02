"""LINE Messaging API（push message）クライアント。

- 月間送信予算をローカル状態ファイルで管理（制約分析 4.2）。
- 送信失敗は指数バックオフで再試行。最終的に失敗したら例外ではなく False を返し、
  呼び出し側でログに残す。
- トークンと userId は環境変数から。ここでは受け取るだけ。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_TEXT_LEN = 5000  # LINE の text message 上限


class MonthlyBudget:
    """月ごとの送信数をファイルで永続化する。プロセス再起動で予算がリセットされないように。"""

    def __init__(self, path: Path, limit: int):
        self.path = Path(path)
        self.limit = limit

    def _load(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            log.warning("予算ファイルが壊れている。リセット: %s", self.path)
            return {}

    @staticmethod
    def _key(now: datetime | None = None) -> str:
        now = now or datetime.now(tz=UTC)
        return now.strftime("%Y-%m")

    def used(self, now: datetime | None = None) -> int:
        return self._load().get(self._key(now), 0)

    def remaining(self, now: datetime | None = None) -> int:
        return max(0, self.limit - self.used(now))

    def consume(self, n: int = 1, now: datetime | None = None) -> None:
        data = self._load()
        k = self._key(now)
        data[k] = data.get(k, 0) + n
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))


class LineNotifier:
    def __init__(
        self,
        channel_access_token: str,
        target_user_id: str,
        budget: MonthlyBudget,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        sleep_fn=time.sleep,  # type: ignore[no-untyped-def]
    ):
        if not channel_access_token or not target_user_id:
            raise ValueError("LINE のトークンと userId が未設定")
        self.token = channel_access_token
        self.to = target_user_id
        self.budget = budget
        self.client = client or httpx.Client(timeout=10.0)
        self.max_retries = max_retries
        self._sleep = sleep_fn

    def push_text(self, text: str, *, reserve_for_p0: int = 0) -> bool:
        """テキストを 1 通送る。予算が reserve_for_p0 以下なら送らない（P0 用に温存）。"""
        if self.budget.remaining() <= reserve_for_p0:
            log.warning("LINE 予算不足のため送信スキップ (残 %d)", self.budget.remaining())
            return False
        text = text[:MAX_TEXT_LEN]
        body = {"to": self.to, "messages": [{"type": "text", "text": text}]}
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self.client.post(PUSH_URL, json=body, headers=headers)
            except httpx.HTTPError as e:
                log.warning("LINE 送信エラー (%d 回目): %s", attempt + 1, e)
            else:
                if r.status_code == 200:
                    self.budget.consume(1)
                    return True
                if r.status_code in (400, 401, 403):
                    log.error("LINE 送信拒否 %d: %s", r.status_code, r.text[:200])
                    return False  # 再試行しても無駄
                log.warning("LINE 送信失敗 %d (%d 回目)", r.status_code, attempt + 1)
            if attempt < self.max_retries:
                self._sleep(delay)
                delay *= 2
        return False
