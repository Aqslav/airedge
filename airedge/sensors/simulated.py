"""Муляж датчика: генерує правдоподібні випадкові дані + імітує збої."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

from ..models import METRICS, Reading
from .base import Sensor, SensorError


@dataclass(frozen=True)
class MetricProfile:
    """Параметри випадкового блукання з поверненням до середнього."""

    mean: float
    sigma: float  # крок шуму
    theta: float  # швидкість повернення до середнього (0..1)


# Щоб додати метрику в симулятор — додайте її в models.METRICS та сюди.
PROFILES: dict[str, MetricProfile] = {
    "pm25": MetricProfile(12.0, 2.5, 0.10),
    "pm10": MetricProfile(25.0, 5.0, 0.10),
    "co2": MetricProfile(600.0, 30.0, 0.05),
    "temperature": MetricProfile(22.0, 0.3, 0.05),
    "humidity": MetricProfile(45.0, 1.2, 0.05),
}
# У «епізоді забруднення» середнє зростає в стільки разів.
EPISODE_FACTOR = {"pm25": 5.0, "pm10": 4.0, "co2": 2.5}


@dataclass(frozen=True)
class FaultProfile:
    """Ймовірності збоїв за один виклик read()."""

    dropout_rate: float = 0.0  # датчик «мовчить» -> SensorError
    glitch_rate: float = 0.0  # некоректне значення (NaN, мінус, викид)
    episode_rate: float = 0.0  # старт епізоду забруднення (тригерить сповіщення)

    def __post_init__(self):
        for name in ("dropout_rate", "glitch_rate", "episode_rate"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} має бути в [0, 1]")


class SimulatedSensor(Sensor):
    """Імітатор багатометричного датчика якості повітря.

    seed робить послідовність відтворюваною (важливо для тестів і демо).
    """

    def __init__(
        self,
        device_id: str,
        seed: int | None = None,
        faults: FaultProfile | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.device_id = device_id
        self._rng = random.Random(seed)
        self._faults = faults or FaultProfile()
        self._clock = clock
        self._state = {metric: prof.mean for metric, prof in PROFILES.items()}
        self._episode_left = 0

    def _glitch_value(self, metric: str) -> float:
        spec = METRICS[metric]
        return self._rng.choice((float("nan"), -999.0, spec.max_value * 10))

    def _step(self, metric: str) -> float:
        prof = PROFILES[metric]
        target = prof.mean
        if self._episode_left > 0:
            target *= EPISODE_FACTOR.get(metric, 1.0)
        value = self._state[metric]
        value += prof.theta * (target - value) + self._rng.gauss(0.0, prof.sigma)
        spec = METRICS[metric]
        value = min(max(value, spec.min_value), spec.max_value)
        self._state[metric] = value
        return round(value, 2)

    def read(self) -> list[Reading]:
        if self._rng.random() < self._faults.dropout_rate:
            raise SensorError(f"{self.device_id}: датчик не відповідає")
        if self._episode_left == 0 and self._rng.random() < self._faults.episode_rate:
            self._episode_left = self._rng.randint(15, 40)

        ts = self._clock()
        readings = []
        for metric in PROFILES:
            value = self._step(metric)
            if self._rng.random() < self._faults.glitch_rate:
                value = self._glitch_value(metric)
            readings.append(Reading(self.device_id, metric, value, ts))
        if self._episode_left > 0:
            self._episode_left -= 1
        return readings
