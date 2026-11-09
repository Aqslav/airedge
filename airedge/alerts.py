"""Правила сповіщень із гістерезисом (щоб не «мигати» біля порога)."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from .models import METRICS, Alert, Reading

_RANK = {"ok": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class AlertRule:
    """Пороги для метрики."""

    metric: str
    warning: float
    critical: float


DEFAULT_RULES = (
    AlertRule("pm25", 35.0, 55.0),
    AlertRule("pm10", 100.0, 250.0),
    AlertRule("co2", 1000.0, 2000.0),
    AlertRule("temperature", 30.0, 38.0),
    AlertRule("humidity", 70.0, 85.0),
)


class AlertEngine:
    """Стан-машина по парі (пристрій, метрика): ok -> warning -> critical.

    Сповіщення генерується лише при ЗМІНІ рівня. Зниження рівня можливе,
    тільки якщо значення впало нижче порога на `hysteresis` (частка, 0..1).
    """

    def __init__(self, rules=DEFAULT_RULES, hysteresis: float = 0.05):
        if not 0 <= hysteresis < 1:
            raise ValueError("hysteresis має бути в інтервалі [0, 1)")
        for rule in rules:
            if rule.warning >= rule.critical:
                raise ValueError(f"warning має бути < critical для {rule.metric}")
        self._rules = {rule.metric: rule for rule in rules}
        self._hysteresis = hysteresis
        self._state: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def _classify(self, rule: AlertRule, value: float, current: str) -> str:
        if value >= rule.critical:
            raw = "critical"
        elif value >= rule.warning:
            raw = "warning"
        else:
            raw = "ok"
        if _RANK[raw] >= _RANK[current]:
            return raw
        threshold = rule.critical if current == "critical" else rule.warning
        if value < threshold * (1 - self._hysteresis):
            return raw
        return current

    def evaluate(self, reading: Reading) -> Alert | None:
        """Повертає Alert, якщо рівень для (пристрій, метрика) змінився."""
        rule = self._rules.get(reading.metric)
        if rule is None:
            return None
        key = (reading.device, reading.metric)
        with self._lock:
            current = self._state.get(key, "ok")
            new = self._classify(rule, reading.value, current)
            if new == current:
                return None
            self._state[key] = new
        level = "recovered" if new == "ok" else new
        unit = METRICS[reading.metric].unit
        message = f"{reading.metric}={reading.value} {unit}: {level}"
        return Alert(reading.device, reading.metric, level, reading.value, reading.ts, message)
