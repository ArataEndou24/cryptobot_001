"""ドローダウン・ラダー（制約分析 3.1）。研究と本番で共通。

- 累積 DD が閾値を超えるごとに規模係数を下げる。最終段は全停止（係数 0、再開は人間の承認のみ）。
- 係数は「戻っても自動では上げない」（ヒステリシス）。回復後の再拡大は人間が判断する。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DrawdownLadder:
    steps: list[tuple[float, float]] = field(
        default_factory=lambda: [(0.20, 0.5), (0.35, 0.25), (0.50, 0.0)]
    )
    scale: float = 1.0
    halted: bool = False
    triggered: list[float] = field(default_factory=list)

    def update(self, drawdown: float) -> float:
        """現在の DD（0〜1）を与え、規模係数を返す。"""
        for threshold, scale in self.steps:
            if drawdown >= threshold and threshold not in self.triggered:
                self.triggered.append(threshold)
                self.scale = min(self.scale, scale)
                if scale == 0.0:
                    self.halted = True
        return self.scale

    def reset(self) -> None:
        """人間の承認による再開。"""
        self.scale, self.halted, self.triggered = 1.0, False, []
