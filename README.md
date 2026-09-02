# cryptobot

暗号資産自動売買アルゴリズム。完全新規プロジェクト。

## 文書
- 設計書: [`docs/00_DESIGN.md`](docs/00_DESIGN.md)
- 決定事項ログ: [`docs/01_DECISIONS.md`](docs/01_DECISIONS.md)
- 制約条件の分析（日本在住・小資金・LINE 通知）: [`docs/02_CONSTRAINTS.md`](docs/02_CONSTRAINTS.md)
- バックテスター詳細仕様: [`docs/03_BACKTESTER.md`](docs/03_BACKTESTER.md)
- データノート（品質所見の記録）: [`docs/04_DATA_NOTES.md`](docs/04_DATA_NOTES.md)

## 現在のフェーズ
**1. 基盤** 完了（BTC/ETH/SOL/HYPE の 2020 年〜2026-08 の 1 分足と資金調達率を取得、品質レポート済み）。
**2. バックテスター** 進行中（Bar モードのエンジン第 1 版まで実装。次は検証プロトコル WFO/CPCV とレポート）。

実装済み:
- `core/`: 時刻規約（UTC 強制、`as_of` 境界）、取引所非依存の基本型、OMS 状態、取引所仕様による丸め
- `exchanges/base.py`: 履歴データ源・市場データ・発注の抽象インターフェース
- `data/binance_vision.py`: Binance 公開履歴データ（USDT 無期限）の取得。チェックサム検証、時刻単位の自動正規化
- `data/store.py`: Parquet ストア（生データは上書き禁止、原子的書き込み）
- `data/bars.py`: 時間バーのリサンプル、ドルバー
- `data/quality.py`: 欠損・重複・順序・OHLC 整合・異常ジャンプ・出来高ゼロの品質ゲート
- `notify/`: LINE Messaging API push（月間予算・再試行）、重大度別アラートルーティング（重複抑止・バッチ・予算温存）
- `exchanges/hyperliquid.py`: 第 1 系統。仕様（数量刻み・価格刻み・最小注文）取得、ユニバース選定、直近の足と資金調達率

- `backtest/`: 口座モデル、Bar モード約定判定、執行アルゴ、イベント駆動エンジン、指標
- `strategies/base.py`, `risk/ladder.py`: 戦略インターフェース、DD ラダー（研究・本番共通）

未実装（次）: 検証プロトコル（WFO/CPCV/デフレートシャープ）、特徴量、戦略研究、リアルタイム市場データ、発注

## セットアップ
```bash
uv sync --extra dev
cp .env.example .env   # 必要に応じて編集
uv run pytest -q
uv run ruff check src tests scripts
```

## 使い方
```bash
# 履歴データ一括取得（Binance 公開データ。月次ファイル。取引ではないので居住地の制約なし）
uv run python scripts/fetch_history.py --symbols BTCUSDT ETHUSDT SOLUSDT HYPEUSDT \
    --start 2020-01-01 --end 2026-09-01 --klines-monthly --funding

# 日次ファイル（約定・OI などを短期間だけ）
uv run python scripts/fetch_history.py --symbols ETHUSDT \
    --start 2026-08-01 --end 2026-08-08 --klines --agg-trades --metrics

# バー生成と品質レポート
uv run python scripts/build_bars.py --symbols BTCUSDT ETHUSDT --intervals 5m 15m 1h 4h 1d --dollar 2e6
```

データは `data/`（git 管理外）に保存されます。
