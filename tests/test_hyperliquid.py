from decimal import Decimal

import httpx

from cryptobot.exchanges.hyperliquid import AssetContext, HyperliquidInfo, price_tick


def test_price_tick_rules():
    # BTC: 77,700 → 有効数字 5 桁で整数刻み、szDecimals=5 → 小数桁上限 1 → 刻み 1
    assert price_tick(77700.0, 5) == Decimal("1")
    # ETH: 2424 → 有効数字で小数 1 桁、上限 6−4=2 → 0.1
    assert price_tick(2424.0, 4) == Decimal("0.1")
    # SOL: 100.3 → 有効数字で小数 2 桁、上限 4 → 0.01
    assert price_tick(100.3, 2) == Decimal("0.01")
    # 1 未満: 0.004333, szDecimals=0 → 先頭ゼロ 2 + 5 = 7 桁だが上限 6 → 1e-6
    assert price_tick(0.004333, 0) == Decimal("0.000001")
    # 0.4312, szDecimals=1 → 有効数字で 5 桁、上限 5 → 1e-5
    assert price_tick(0.4312, 1) == Decimal("0.00001")


def test_sizing_steps_small_capital():
    btc = AssetContext("BTC", 5, 40, 77700.0, 2.7e9, 38000.0, 1.25e-5, False)
    assert abs(btc.lot_notional_usd - 0.777) < 1e-9
    assert btc.min_order_usd == 10.0
    assert btc.sizing_steps(700) == 70.0


def _mock_info(payload):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return HyperliquidInfo(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_select_universe_and_specs():
    payload = [
        {
            "universe": [
                {"szDecimals": 5, "name": "BTC", "maxLeverage": 40},
                {"szDecimals": 4, "name": "ETH", "maxLeverage": 25},
                {"szDecimals": 1, "name": "OLD", "maxLeverage": 10, "isDelisted": True},
                {"szDecimals": 0, "name": "THIN", "maxLeverage": 3},
            ]
        },
        [
            {
                "markPx": "77700",
                "dayNtlVlm": "2.7e9",
                "openInterest": "38000",
                "funding": "0.0000125",
            },
            {
                "markPx": "2424",
                "dayNtlVlm": "1.0e9",
                "openInterest": "900000",
                "funding": "0.0000125",
            },
            {"markPx": "1", "dayNtlVlm": "1e9", "openInterest": "1", "funding": "0"},
            {"markPx": "50", "dayNtlVlm": "1e6", "openInterest": "1", "funding": "0"},
        ],
    ]
    info = _mock_info(payload)
    uni = info.select_universe(700)
    assert [c.name for c in uni] == ["BTC", "ETH"]
    specs = info.specs()
    assert "OLD" not in specs
    eth = specs["ETH"]
    assert eth.step_size == Decimal("0.0001") and eth.tick_size == Decimal("0.1")
    assert eth.min_notional == Decimal("10")
    ok, _ = eth.is_valid_order(Decimal("0.004"), Decimal("2424"))
    assert not ok  # 9.7 USD < 10
    ok, _ = eth.is_valid_order(Decimal("0.005"), Decimal("2424"))
    assert ok


def test_candles_and_funding_parsing():
    candles = [
        {
            "t": 1,
            "T": 60000,
            "s": "ETH",
            "i": "1m",
            "o": "1",
            "c": "2",
            "h": "3",
            "l": "0.5",
            "v": "10",
            "n": 4,
        }
    ]
    info = _mock_info(candles)
    df = info.candles("ETH", "1m", 0, 60000)
    assert df.height == 1 and df["quote_volume"][0] == 20.0
    info = _mock_info([{"coin": "ETH", "fundingRate": "0.0000125", "premium": "0", "time": 1000}])
    f = info.funding_history("ETH", 0)
    assert f["interval_hours"][0] == 1 and abs(f["rate"][0] - 1.25e-5) < 1e-12
