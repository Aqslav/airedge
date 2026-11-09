"""Інтерфейс датчика. Реальний драйвер (I2C/UART/MQTT) реалізує той самий контракт."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Reading


class SensorError(RuntimeError):
    """Датчик не відповів або повернув помилку зчитування."""


class Sensor(ABC):
    """Джерело показань."""

    device_id: str

    @abstractmethod
    def read(self) -> list[Reading]:
        """Повертає набір показань або кидає SensorError."""
