import math

import pytest

from airedge.models import METRICS
from airedge.sensors import FaultProfile, SensorError, SimulatedSensor
from airedge.validation import validate


def fixed_clock():
    return 1_700_000_000.0


def test_sensor_returns_all_metrics_in_range():
    sensor = SimulatedSensor("s1", seed=1, clock=fixed_clock)
    for _ in range(200):
        readings = sensor.read()
        assert {r.metric for r in readings} == set(METRICS)
        for reading in readings:
            validate(reading, fixed_clock())  # нормальні дані завжди валідні


def test_same_seed_is_reproducible():
    a = SimulatedSensor("s1", seed=42, clock=fixed_clock)
    b = SimulatedSensor("s1", seed=42, clock=fixed_clock)
    assert [a.read() for _ in range(10)] == [b.read() for _ in range(10)]


def test_different_seeds_differ():
    a = SimulatedSensor("s1", seed=1, clock=fixed_clock).read()
    b = SimulatedSensor("s1", seed=2, clock=fixed_clock).read()
    assert a != b


def test_dropout_raises_sensor_error():
    sensor = SimulatedSensor("s1", seed=1, faults=FaultProfile(dropout_rate=1.0))
    with pytest.raises(SensorError):
        sensor.read()


def test_glitches_are_produced():
    sensor = SimulatedSensor("s1", seed=3, faults=FaultProfile(glitch_rate=1.0), clock=fixed_clock)
    values = [r.value for _ in range(20) for r in sensor.read()]
    bad = [v for v in values if math.isnan(v) or v < 0 or v >= 5000]
    assert bad, "glitch_rate=1.0 має давати некоректні значення"


def test_pollution_episode_raises_pm25():
    calm = SimulatedSensor("s1", seed=5, clock=fixed_clock)
    smoky = SimulatedSensor("s1", seed=5, faults=FaultProfile(episode_rate=1.0), clock=fixed_clock)

    def pm25_after(sensor, n=40):
        value = None
        for _ in range(n):
            value = next(r.value for r in sensor.read() if r.metric == "pm25")
        return value

    assert pm25_after(smoky) > pm25_after(calm) * 2


@pytest.mark.parametrize("kw", [{"dropout_rate": -0.1}, {"glitch_rate": 1.1}, {"episode_rate": 2}])
def test_fault_profile_validation(kw):
    with pytest.raises(ValueError):
        FaultProfile(**kw)
