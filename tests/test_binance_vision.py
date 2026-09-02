import hashlib
import io
import zipfile
from datetime import date

import httpx

from cryptobot.data.binance_vision import BinanceVisionSource, parse_csv_bytes


def _zip(csv: bytes, name: str = "x.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv)
    return buf.getvalue()


def test_parse_with_and_without_header():
    cols = ["a", "b"]
    assert parse_csv_bytes(b"a,b\n1,2\n", cols).row(0) == ("1", "2")
    assert parse_csv_bytes(b"1,2\n3,4\n", cols).height == 2


def test_fetch_klines_with_checksum_and_microsecond_ts():
    csv = (
        b"open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        b"1704067200000000,42000,42100,41900,42050,10,1704067259999999,420000,100,6,252000,0\n"
    )
    blob = _zip(csv)
    sha = hashlib.sha256(blob).hexdigest()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{sha}  file.zip\n")
        if req.url.path.endswith(".zip"):
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    src = BinanceVisionSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    df = src.fetch_klines("BTCUSDT", "1m", date(2024, 1, 1))
    assert df is not None and df.height == 1
    assert df["open_ts_ms"][0] == 1704067200000  # マイクロ秒 → ミリ秒
    assert df["close_ts_ms"][0] == 1704067259999
    assert df["taker_buy_volume"][0] == 6.0


def test_checksum_mismatch_raises():
    blob = _zip(b"1,2,3,4,5,6,7,8,9,10,11,12\n")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text="deadbeef  file.zip\n")
        return httpx.Response(200, content=blob)

    src = BinanceVisionSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    import pytest

    with pytest.raises(ValueError):
        src.fetch_klines("BTCUSDT", "1m", date(2024, 1, 1))


def test_404_returns_none():
    src = BinanceVisionSource(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    )
    assert src.fetch_agg_trades("BTCUSDT", date(2019, 1, 1)) is None
