"""Валідація вхідних показань (захист від «сміття» з датчиків)."""
from __future__ import annotations

import math
import re
import time

from .models import METRICS, Reading

DEVICE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_FUTURE_SECONDS = 300.0


class ValidationError(ValueError):
    """Показання не пройшло валідацію."""


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(reading: Reading, now: float | None = None) -> Reading:
    """Повертає показання, якщо воно коректне, інакше кидає ValidationError."""
    now = time.time() if now is None else now

    if not isinstance(reading.device, str) or not DEVICE_RE.match(reading.device):
        raise ValidationError(f"некоректне ім'я пристрою: {reading.device!r}")

    spec = METRICS.get(reading.metric)
    if spec is None:
        raise ValidationError(f"невідома метрика: {reading.metric!r}")

    if not _is_number(reading.value) or not math.isfinite(reading.value):
        raise ValidationError(f"значення не є скінченним числом: {reading.value!r}")
    if not spec.min_value <= reading.value <= spec.max_value:
        raise ValidationError(
            f"{reading.metric}={reading.value} поза діапазоном "
            f"[{spec.min_value}, {spec.max_value}]"
        )

    if not _is_number(reading.ts) or not math.isfinite(reading.ts) or reading.ts <= 0:
        raise ValidationError(f"некоректна мітка часу: {reading.ts!r}")
    if reading.ts > now + MAX_FUTURE_SECONDS:
        raise ValidationError("мітка часу занадто далеко в майбутньому")

    return reading
