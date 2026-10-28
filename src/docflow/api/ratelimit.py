"""Ограничение частоты попыток входа: скользящее окно в памяти процесса.

Счётчики не переживают перезапуск и не общие между процессами — для одного API-контейнера этого хватает.
Ключи (IP, email) выбирает клиент, поэтому раз в `sweep_every` попыток все истёкшие ключи вычищаются: иначе
поток уникальных ключей растил бы словарь без предела.
"""

import hmac
import time
from collections import deque

from fastapi import Request

from docflow.config import Settings


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float, sweep_every: int = 1000) -> None:
        self.limit = limit
        self.window_s = window_s
        self.sweep_every = sweep_every
        self._hits: dict[str, deque[float]] = {}
        self._since_sweep = 0

    def _sweep(self) -> None:
        """Удалить ключи, у которых все попытки старше окна (у живых очередь не трогаем)."""
        cutoff = time.monotonic() - self.window_s
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._hits[key]

    def _fresh(self, key: str) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        cutoff = time.monotonic() - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            del self._hits[key]
        return hits

    def hit(self, key: str) -> bool:
        """True — разрешено, попытка учтена; False — лимит исчерпан."""
        self._since_sweep += 1
        if self._since_sweep >= self.sweep_every:
            self._since_sweep = 0
            self._sweep()
        hits = self._fresh(key)
        if len(hits) >= self.limit:
            return False
        hits.append(time.monotonic())
        self._hits[key] = hits
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


def client_ip(request: Request, settings: Settings) -> str:
    """Адрес клиента. Заголовку X-Client-IP верим только от сайта, знающего internal_token."""
    token = request.headers.get("x-internal-token", "")
    forwarded = request.headers.get("x-client-ip", "").strip()
    if settings.internal_token and forwarded and hmac.compare_digest(token.encode(), settings.internal_token.encode()):
        return forwarded
    return request.client.host if request.client else "unknown"
