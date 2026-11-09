import pytest

from airedge.models import Reading
from airedge.validation import ValidationError, validate

NOW = 1_700_000_000.0


def make(**kw):
    base = {"device": "sensor-01", "metric": "pm25", "value": 10.0, "ts": NOW}
    base.update(kw)
    return Reading(**base)


def test_valid_reading_passes():
    reading = make()
    assert validate(reading, NOW) is reading


@pytest.mark.parametrize("value", [0.0, 1000.0])
def test_boundary_values_are_valid(value):
    validate(make(value=value), NOW)


@pytest.mark.parametrize(
    "kw",
    [
        {"value": -0.01},
        {"value": 1000.01},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": "10"},
        {"value": None},
        {"value": True},
        {"metric": "radon"},
        {"device": ""},
        {"device": "bad name!"},
        {"device": "x" * 65},
        {"device": "a'; DROP TABLE readings;--"},
        {"ts": 0},
        {"ts": -5},
        {"ts": float("nan")},
        {"ts": "now"},
        {"ts": NOW + 10_000},
    ],
)
def test_invalid_readings_rejected(kw):
    with pytest.raises(ValidationError):
        validate(make(**kw), NOW)


def test_default_now_is_used():
    with pytest.raises(ValidationError):
        validate(make(ts=4_000_000_000_000.0))
