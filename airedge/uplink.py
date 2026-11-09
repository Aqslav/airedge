"""Відправка даних «в хмару» за схемою store-and-forward з експоненційним backoff."""
from __future__ import annotations

import json
import random
import time
from abc import ABC, abstractmethod
from typing import Callable

from .storage import Storage


class UplinkError(RuntimeError):
    """Канал до хмари недоступний або відправка не вдалася."""


class Uplink(ABC):
    """Канал відправки пакета показань."""

    @abstractmethod
    def send(self, batch: list[dict]) -> None:
        """Відправляє пакет або кидає UplinkError."""


class FileUplink(Uplink):
    """Симуляція хмари: дописує JSON-рядки у файл; може «втрачати зв'язок»."""

    def __init__(self, path: str, failure_rate: float = 0.0, rng: random.Random | None = None):
        if not 0.0 <= failure_rate <= 1.0:
            raise ValueError("failure_rate має бути в [0, 1]")
        self._path = path
        self._failure_rate = failure_rate
        self._rng = rng or random.Random()

    def send(self, batch: list[dict]) -> None:
        if self._rng.random() < self._failure_rate:
            raise UplinkError("мережа недоступна (симуляція)")
        try:
            with open(self._path, "a", encoding="utf-8") as handle:
                for item in batch:
                    handle.write(json.dumps(item) + "\n")
        except OSError as exc:
            raise UplinkError(f"не вдалося записати у {self._path}: {exc}") from exc


def backoff_delay(failures: int, base: float, cap: float) -> float:
    """Затримка перед наступною спробою: base * 2^failures, але не більше cap."""
    if failures < 0 or base <= 0 or cap < base:
        raise ValueError("потрібно failures >= 0, base > 0, cap >= base")
    return min(cap, base * 2 ** min(failures, 30))


def sync_once(
    storage: Storage,
    uplink: Uplink,
    batch_size: int = 100,
    clock: Callable[[], float] = time.time,
) -> int:
    """Одна спроба відправити пакет. Повертає кількість відправлених показань.

    При помилці канал не втрачає дані: записи лишаються в outbox
    (лічильник attempts збільшується), а UplinkError піднімається вище.
    """
    batch = storage.pending_batch(batch_size)
    if not batch:
        return 0
    ids = [item["outbox_id"] for item in batch]
    payload = [{k: v for k, v in item.items() if k != "outbox_id"} for item in batch]
    try:
        uplink.send(payload)
    except UplinkError:
        storage.mark_failed(ids)
        raise
    storage.mark_sent(ids, clock())
    return len(batch)
