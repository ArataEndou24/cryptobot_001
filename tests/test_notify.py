import httpx

from cryptobot.notify.line import LineNotifier, MonthlyBudget
from cryptobot.notify.router import AlertRouter, Severity


def _notifier(tmp_path, statuses):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(statuses.pop(0))

    budget = MonthlyBudget(tmp_path / "budget.json", limit=5)
    n = LineNotifier(
        "tok",
        "uid",
        budget,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=lambda s: None,
    )
    return n, calls, budget


def test_push_consumes_budget(tmp_path):
    n, calls, budget = _notifier(tmp_path, [200])
    assert n.push_text("hi")
    assert budget.used() == 1 and len(calls) == 1


def test_retry_on_5xx_then_success(tmp_path):
    n, calls, budget = _notifier(tmp_path, [500, 429, 200])
    assert n.push_text("hi")
    assert len(calls) == 3 and budget.used() == 1


def test_no_retry_on_401(tmp_path):
    n, calls, budget = _notifier(tmp_path, [401])
    assert not n.push_text("hi")
    assert len(calls) == 1 and budget.used() == 0


def test_budget_reserve(tmp_path):
    n, calls, budget = _notifier(tmp_path, [200, 200, 200, 200, 200])
    budget.consume(4)  # 残 1
    assert not n.push_text("p1", reserve_for_p0=1)  # P0 用に温存
    assert n.push_text("p0", reserve_for_p0=0)


def test_router_dedup_and_batch():
    sent = []
    clock = [1000.0]
    r = AlertRouter(
        send=lambda t, res: sent.append((t, res)) or True,
        remaining_budget=lambda: 100,
        _clock=lambda: clock[0],
    )
    r.emit(Severity.P0, "dd", "DD −20%")
    r.emit(Severity.P0, "dd", "DD −20%")  # 重複
    assert len(sent) == 1
    clock[0] += 700
    r.emit(Severity.P0, "dd", "DD −20%")  # 窓を過ぎた
    assert len(sent) == 2
    r.emit(Severity.P1, "lag", "データ遅延")
    r.emit(Severity.P1, "rate", "レート制限")
    assert len(sent) == 2
    r.flush()
    assert len(sent) == 3 and "データ遅延" in sent[-1][0] and "レート制限" in sent[-1][0]


def test_router_budget_protection():
    sent = []
    r = AlertRouter(
        send=lambda t, res: sent.append(t) or True, remaining_budget=lambda: 10, p0_reserve=30
    )
    r.emit(Severity.P1, "x", "x")
    r.flush()
    assert sent == []  # 予算温存
    r.emit(Severity.P0, "kill", "キルスイッチ")
    assert len(sent) == 1
