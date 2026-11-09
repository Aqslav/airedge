"""Конвеєр обробки: черга -> валідація -> збереження -> сповіщення."""
from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections import defaultdict
from typing import Callable

from .alerts import AlertEngine
from .models import Reading
from .storage import Storage
from .validation import ValidationError, validate

LOG = logging.getLogger(__name__)


class Counters:
    """Потокобезпечні лічильники подій."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, int] = defaultdict(int)

    def incr(self, name: str, amount: int = 1) -> None:
        """Збільшує лічильник."""
        with self._lock:
            self._data[name] += amount

    def snapshot(self) -> dict[str, int]:
        """Копія поточних значень."""
        with self._lock:
            return dict(self._data)


class Pipeline:
    """Приймає показання в обмежену чергу (backpressure) і обробляє їх."""

    def __init__(
        self,
        storage: Storage,
        alert_engine: AlertEngine,
        counters: Counters | None = None,
        queue_size: int = 1000,
        clock: Callable[[], float] = time.time,
    ):
        self._storage = storage
        self._alerts = alert_engine
        self._queue: queue.Queue[Reading] = queue.Queue(maxsize=queue_size)
        self._clock = clock
        self.counters = counters or Counters()

    def submit(self, reading: Reading) -> bool:
        """Кладе показання в чергу; при переповненні відкидає і рахує."""
        try:
            self._queue.put_nowait(reading)
        except queue.Full:
            self.counters.incr("dropped")
            return False
        return True

    def backlog(self) -> int:
        """Скільки показань чекає в черзі."""
        return self._queue.qsize()

    def process(self, reading: Reading) -> bool:
        """Повна обробка одного показання. True — якщо збережено як нове."""
        try:
            validate(reading, self._clock())
        except ValidationError as exc:
            LOG.warning("показання відхилено: %s", exc)
            self.counters.incr("rejected")
            return False
        try:
            saved = self._storage.save_reading(reading)
            if not saved:
                self.counters.incr("duplicates")
                return False
            self.counters.incr("accepted")
            alert = self._alerts.evaluate(reading)
            if alert is not None:
                self._storage.save_alert(alert)
                self.counters.incr("alerts")
                LOG.info("СПОВІЩЕННЯ %s %s", alert.device, alert.message)
        except sqlite3.Error as exc:
            LOG.error("помилка сховища: %s", exc)
            self.counters.incr("storage_errors")
            return False
        return True

    def drain(self, timeout: float = 0.1) -> bool:
        """Бере одне показання з черги й обробляє. False — черга порожня."""
        try:
            reading = self._queue.get(timeout=timeout)
        except queue.Empty:
            return False
        self.process(reading)
        return True
