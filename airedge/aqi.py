"""Розрахунок індексу якості повітря (US EPA AQI) для PM2.5 та PM10.

Використано таблиці брейкпоінтів EPA (PM2.5 — редакція 2024 року).
Формула: I = (I_hi - I_lo) / (BP_hi - BP_lo) * (C - BP_lo) + I_lo.
"""
from __future__ import annotations

import math

# (BP_lo, BP_hi, I_lo, I_hi)
_PM25 = (
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 400),
    (325.5, 500.4, 401, 500),
)
_PM10 = (
    (0, 54, 0, 50),
    (55, 154, 51, 100),
    (155, 254, 101, 150),
    (255, 354, 151, 200),
    (355, 424, 201, 300),
    (425, 504, 301, 400),
    (505, 604, 401, 500),
)
# метрика -> (таблиця, кількість знаків після коми, до якої усікається концентрація)
BREAKPOINTS = {"pm25": (_PM25, 1), "pm10": (_PM10, 0)}

CATEGORIES = (
    (50, "Добре"),
    (100, "Помірно"),
    (150, "Шкідливо для чутливих груп"),
    (200, "Шкідливо"),
    (300, "Дуже шкідливо"),
    (500, "Небезпечно"),
)


def compute_aqi(metric: str, value: float) -> int | None:
    """AQI для однієї метрики; None, якщо для метрики AQI не визначено."""
    if metric not in BREAKPOINTS:
        return None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"концентрація має бути невід'ємним числом: {value!r}")

    table, digits = BREAKPOINTS[metric]
    factor = 10 ** digits
    conc = math.floor(value * factor + 1e-9) / factor  # EPA усікає, а не округлює
    if conc > table[-1][1]:
        return 500
    for low, high, i_low, i_high in table:
        if conc <= high:
            return int((i_high - i_low) / (high - low) * (conc - low) + i_low + 0.5)
    return 500  # недосяжно, але робить функцію тотальною


def category(aqi: int) -> str:
    """Словесна категорія за значенням AQI."""
    if aqi < 0:
        raise ValueError("AQI не може бути від'ємним")
    for upper, label in CATEGORIES:
        if aqi <= upper:
            return label
    return CATEGORIES[-1][1]


def overall_aqi(values: dict[str, float]) -> tuple[int, str] | None:
    """Загальний AQI = максимум по доступних метриках. Повертає (aqi, метрика)."""
    scored = [
        (aqi, metric)
        for metric, value in values.items()
        if (aqi := compute_aqi(metric, value)) is not None
    ]
    return max(scored) if scored else None
