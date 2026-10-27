"""Тонкая обёртка над очередью: arq в проде, список в тестах."""

from datetime import timedelta
from typing import Protocol

PROCESS_DOCUMENT = "process_document"
DELIVER_WEBHOOK = "deliver_webhook"


class Enqueuer(Protocol):
    async def enqueue(self, job: str, *args, defer_by: int | None = None) -> None: ...


class ArqEnqueuer:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def enqueue(self, job: str, *args, defer_by: int | None = None) -> None:
        await self._pool.enqueue_job(job, *args, _defer_by=timedelta(seconds=defer_by) if defer_by else None)


class ListEnqueuer:
    """Для тестов: ничего не исполняет, только запоминает вызовы."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, int | None]] = []

    async def enqueue(self, job: str, *args, defer_by: int | None = None) -> None:
        self.calls.append((job, args, defer_by))
