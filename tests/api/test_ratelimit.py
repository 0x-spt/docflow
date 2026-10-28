from docflow.api import ratelimit
from docflow.api.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limit_and_window(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit.time, "monotonic", clock)
    lim = SlidingWindowLimiter(limit=2, window_s=10)
    assert lim.hit("a") and lim.hit("a")
    assert not lim.hit("a")
    clock.now += 11
    assert lim.hit("a")


def test_stale_keys_are_swept(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit.time, "monotonic", clock)
    lim = SlidingWindowLimiter(limit=5, window_s=10, sweep_every=100)
    for i in range(1000):  # ключи выбирает атакующий: каждый новый
        lim.hit(f"k{i}")
    clock.now += 11
    for i in range(100):
        lim.hit(f"fresh{i}")
    # старые ключи вычищены полной уборкой, остались только свежие
    assert len(lim._hits) == 100
    assert all(k.startswith("fresh") for k in lim._hits)


def test_sweep_keeps_live_counters(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit.time, "monotonic", clock)
    lim = SlidingWindowLimiter(limit=2, window_s=10, sweep_every=3)
    assert lim.hit("victim") and lim.hit("victim")
    for i in range(10):
        lim.hit(f"x{i}")
    assert not lim.hit("victim")  # уборка не сбросила живой счётчик
