"""口座・ポジションモデル（バックテスター仕様 2 章）。研究と本番の損益計算で共通に使う。

不変条件:
- equity = cash + Σ unrealized
- 手数料・funding・実現損益はすべて cash に反映し、履歴に残す
- 清算は「バックテスト失敗」扱い。ここでは検知のみ行い、処理はエンジン側
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptobot.core.types import Side


@dataclass(slots=True)
class Position:
    qty: float = 0.0  # 符号付き。ロング正
    avg_price: float = 0.0

    @property
    def is_flat(self) -> bool:
        return abs(self.qty) < 1e-12

    def unrealized(self, mark: float) -> float:
        return self.qty * (mark - self.avg_price)

    def notional(self, mark: float) -> float:
        return abs(self.qty) * mark


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    ts_ms: int
    kind: str  # "fee" | "realized" | "funding" | "liquidation_fee"
    instrument_key: str
    amount: float  # cash への加減（負なら支払い）
    note: str = ""


@dataclass
class Account:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    ledger: list[LedgerEntry] = field(default_factory=list)
    peak_equity: float = 0.0

    def __post_init__(self) -> None:
        self.peak_equity = max(self.peak_equity, self.cash)

    # ---- 参照 ----
    def position(self, key: str) -> Position:
        return self.positions.setdefault(key, Position())

    def unrealized(self, marks: dict[str, float]) -> float:
        return sum(p.unrealized(marks[k]) for k, p in self.positions.items() if not p.is_flat)

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + self.unrealized(marks)

    def gross_notional(self, marks: dict[str, float]) -> float:
        return sum(p.notional(marks[k]) for k, p in self.positions.items() if not p.is_flat)

    def leverage(self, marks: dict[str, float]) -> float:
        eq = self.equity(marks)
        return self.gross_notional(marks) / eq if eq > 0 else float("inf")

    def drawdown(self, marks: dict[str, float]) -> float:
        """直近最高 equity からの下落率（0〜1）。"""
        eq = self.equity(marks)
        self.peak_equity = max(self.peak_equity, eq)
        return 0.0 if self.peak_equity <= 0 else max(0.0, 1 - eq / self.peak_equity)

    # ---- イベント ----
    def apply_fill(
        self,
        key: str,
        side: Side,
        qty: float,
        price: float,
        fee_rate: float,
        ts_ms: int,
        note: str = "",
    ) -> tuple[float, float]:
        """約定を反映。戻り値は (実現損益, 手数料)。

        平均建値法。反対売買で減らす分だけ実現し、超過分は新規建てとして avg_price を更新する。
        """
        if qty <= 0:
            raise ValueError("qty は正")
        pos = self.position(key)
        signed = side.sign * qty
        realized = 0.0
        if pos.is_flat or (pos.qty > 0) == (signed > 0):
            # 同方向: 平均建値を更新
            new_qty = pos.qty + signed
            pos.avg_price = (pos.avg_price * abs(pos.qty) + price * qty) / abs(new_qty)
            pos.qty = new_qty
        else:
            # 反対方向: 既存を減らす分だけ実現
            closing = min(qty, abs(pos.qty))
            realized = closing * (price - pos.avg_price) * (1 if pos.qty > 0 else -1)
            remaining = qty - closing
            pos.qty += signed if remaining == 0 else (closing * side.sign)
            if remaining > 0:
                pos.qty = side.sign * remaining
                pos.avg_price = price
            elif pos.is_flat:
                pos.qty = 0.0
                pos.avg_price = 0.0
        fee = qty * price * fee_rate
        self.cash += realized - fee
        if realized:
            self.ledger.append(LedgerEntry(ts_ms, "realized", key, realized, note))
        self.ledger.append(LedgerEntry(ts_ms, "fee", key, -fee, note))
        return realized, fee

    def apply_funding(self, key: str, mark: float, rate: float, ts_ms: int) -> float:
        """資金調達。正の rate はロングが支払う。戻り値は cash への加減額。"""
        pos = self.positions.get(key)
        if pos is None or pos.is_flat:
            return 0.0
        amount = -pos.qty * mark * rate
        self.cash += amount
        self.ledger.append(LedgerEntry(ts_ms, "funding", key, amount))
        return amount

    def is_liquidated(self, marks: dict[str, float], mmr: dict[str, float]) -> bool:
        """維持証拠金割れ。mmr は銘柄ごとの維持証拠金率。"""
        req = sum(p.notional(marks[k]) * mmr[k] for k, p in self.positions.items() if not p.is_flat)
        return req > 0 and self.equity(marks) < req

    def liquidate_all(self, marks: dict[str, float], fee_rate: float, ts_ms: int) -> None:
        for k, p in list(self.positions.items()):
            if p.is_flat:
                continue
            qty = abs(p.qty)
            side = Side.SELL if p.qty > 0 else Side.BUY
            self.apply_fill(k, side, qty, marks[k], 0.0, ts_ms, note="liquidation")
            fee = qty * marks[k] * fee_rate
            self.cash -= fee
            self.ledger.append(LedgerEntry(ts_ms, "liquidation_fee", k, -fee))

    def totals(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for e in self.ledger:
            out[e.kind] = out.get(e.kind, 0.0) + e.amount
        return out
