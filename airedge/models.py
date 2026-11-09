"""Базові типи даних edge-платформи."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    """Опис метрики: код, одиниця виміру та допустимий фізичний діапазон."""

    code: str
    unit: str
    min_value: float
    max_value: float


METRICS: dict[str, MetricSpec] = {
    spec.code: spec
    for spec in (
        MetricSpec("pm25", "ug/m3", 0.0, 1000.0),
        MetricSpec("pm10", "ug/m3", 0.0, 2000.0),
        MetricSpec("co2", "ppm", 0.0, 10000.0),
        MetricSpec("temperature", "C", -40.0, 85.0),
        MetricSpec("humidity", "%", 0.0, 100.0),
    )
}


@dataclass(frozen=True)
class Reading:
    """Одне вимірювання одного датчика. ts — unix-час у секундах."""

    device: str
    metric: str
    value: float
    ts: float


@dataclass(frozen=True)
class Alert:
    """Сповіщення про перевищення порога (або повернення до норми)."""

    device: str
    metric: str
    level: str  # warning | critical | recovered
    value: float
    ts: float
    message: str
