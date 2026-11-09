import pytest

from airedge.aqi import category, compute_aqi, overall_aqi


@pytest.mark.parametrize(
    "metric,value,expected",
    [
        ("pm25", 0.0, 0),
        ("pm25", 9.0, 50),       # межа Good
        ("pm25", 9.1, 51),       # початок Moderate
        ("pm25", 35.4, 100),
        ("pm25", 35.5, 101),
        ("pm25", 55.4, 150),
        ("pm25", 125.4, 200),
        ("pm25", 225.4, 300),
        ("pm25", 325.4, 400),
        ("pm25", 500.4, 500),
        ("pm25", 9.04, 50),      # усікання до 0.1, а не округлення
        ("pm25", 9999.0, 500),   # вище таблиці — стеля 500
        ("pm10", 0, 0),
        ("pm10", 54, 50),
        ("pm10", 55, 51),
        ("pm10", 154.9, 100),    # усікання до цілого
        ("pm10", 604, 500),
        ("pm10", 5000, 500),
    ],
)
def test_compute_aqi_breakpoints(metric, value, expected):
    assert compute_aqi(metric, value) == expected


def test_aqi_midpoint_interpolation():
    # (100-51)/(35.4-9.1)*(22.2-9.1)+51 = 75.4 -> 75 (лінійна інтерполяція)
    assert compute_aqi("pm25", 22.2) == 75


def test_aqi_undefined_for_other_metrics():
    assert compute_aqi("co2", 800) is None


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_aqi_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        compute_aqi("pm25", bad)


@pytest.mark.parametrize(
    "aqi,label",
    [(0, "Добре"), (50, "Добре"), (51, "Помірно"), (150, "Шкідливо для чутливих груп"),
     (200, "Шкідливо"), (300, "Дуже шкідливо"), (301, "Небезпечно"), (900, "Небезпечно")],
)
def test_category(aqi, label):
    assert category(aqi) == label


def test_category_negative():
    with pytest.raises(ValueError):
        category(-1)


def test_overall_aqi_takes_worst_metric():
    result = overall_aqi({"pm25": 12.0, "pm10": 200.0, "co2": 500.0})
    assert result == (compute_aqi("pm10", 200.0), "pm10")


def test_overall_aqi_none_without_particulates():
    assert overall_aqi({"co2": 500.0, "humidity": 40.0}) is None
