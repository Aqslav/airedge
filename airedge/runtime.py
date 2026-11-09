"""EdgeNode: керує потоками датчиків, обробки та синхронізації."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from .alerts import AlertEngine
from .pipeline import Counters, Pipeline
from .sensors import Sensor, SensorError
from .storage import Storage
from .uplink import Uplink, UplinkError, backoff_delay, sync_once

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeConfig:
    """Параметри вузла."""

    interval: float = 1.0  # період опитування датчиків, с
    sync_interval: float = 2.0  # період синхронізації з хмарою, с
    sync_cap: float = 30.0  # максимальний backoff, с
    batch_size: int = 100
    queue_size: int = 1000
    alert_engine: AlertEngine | None = None
    clock: Callable[[], float] = time.time

    def __post_init__(self):
        if self.interval <= 0 or self.sync_interval <= 0:
            raise ValueError("interval та sync_interval мають бути > 0")
        if self.batch_size < 1 or self.queue_size < 1:
            raise ValueError("batch_size та queue_size мають бути >= 1")


class EdgeNode:
    """Один edge-вузол: N датчиків -> конвеєр -> SQLite -> хмара."""

    def __init__(
        self,
        storage: Storage,
        sensors: Sequence[Sensor],
        uplink: Uplink,
        config: NodeConfig | None = None,
    ):
        self._config = config or NodeConfig()
        self.storage = storage
        self.pipeline = Pipeline(
            storage,
            self._config.alert_engine or AlertEngine(),
            Counters(),
            self._config.queue_size,
            self._config.clock,
        )
        self._sensors = list(sensors)
        self._uplink = uplink
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def counters(self) -> Counters:
        """Лічильники подій вузла."""
        return self.pipeline.counters

    def _sync(self) -> int:
        cfg = self._config
        return sync_once(self.storage, self._uplink, cfg.batch_size, cfg.clock)

    # --- цикли потоків ---------------------------------------------------
    def _sensor_loop(self, sensor: Sensor) -> None:
        while not self._stop.is_set():
            try:
                for reading in sensor.read():
                    self.pipeline.submit(reading)
            except SensorError as exc:
                self.counters.incr("sensor_errors")
                LOG.warning("збій датчика: %s", exc)
            except Exception:  # pylint: disable=broad-exception-caught
                # потік датчика не має вмирати через несподівану помилку драйвера
                self.counters.incr("sensor_errors")
                LOG.exception("несподівана помилка драйвера %s", sensor.device_id)
            if self._stop.wait(self._config.interval):
                break

    def _consumer_loop(self) -> None:
        while not (self._stop.is_set() and self.pipeline.backlog() == 0):
            self.pipeline.drain(timeout=0.1)

    def _sync_loop(self) -> None:
        failures = 0
        cfg = self._config
        cap = max(cfg.sync_cap, cfg.sync_interval)
        while not self._stop.wait(backoff_delay(failures, cfg.sync_interval, cap)):
            failures = self._sync_step(failures)

    def _sync_step(self, failures: int) -> int:
        """Один цикл синхронізації: шле пакети підряд, поки є повні пакети (catch-up)."""
        try:
            while True:
                sent = self._sync()
                self.counters.incr("synced", sent)
                if sent < self._config.batch_size or self._stop.is_set():
                    break
        except UplinkError as exc:
            self.counters.incr("sync_failures")
            LOG.warning("синхронізація не вдалася: %s", exc)
            return failures + 1
        return 0

    # --- керування -------------------------------------------------------
    def start(self) -> None:
        """Запускає всі потоки."""
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._sensor_loop, args=(s,), name=f"sensor-{s.device_id}")
            for s in self._sensors
        ]
        workers = [
            threading.Thread(target=self._consumer_loop, name="consumer"),
            threading.Thread(target=self._sync_loop, name="sync"),
        ]
        self._threads += workers
        for thread in self._threads:
            thread.daemon = True
            thread.start()

    def stop(self, flush: bool = True) -> None:
        """Коректно зупиняє вузол: дочитує чергу та (за можливості) відправляє залишок."""
        self._stop.set()
        # порядок: спершу датчики (припиняють наповнювати чергу), потім споживач
        for thread in self._threads:
            thread.join(timeout=10)
        if flush:
            self.flush()

    def flush(self, max_rounds: int = 1000) -> None:
        """Відправляє все, що лишилось у outbox; зупиняється при першій помилці каналу."""
        for _ in range(max_rounds):
            try:
                sent = self._sync()
                if sent == 0:
                    return
                self.counters.incr("synced", sent)
            except UplinkError as exc:
                LOG.warning("flush перервано: %s", exc)
                return

    def stats(self) -> dict:
        """Зведена статистика вузла."""
        return {
            "counters": self.counters.snapshot(),
            "queue_backlog": self.pipeline.backlog(),
            "storage": self.storage.stats(),
        }
