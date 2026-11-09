import pytest

from airedge.alerts import AlertEngine, AlertRule
from airedge.models import Reading

RULES = (AlertRule("pm25", 35.0, 55.0),)


def r(value, device="s1", metric="pm25", ts=1.0):
    return Reading(device, metric, value, ts)


def levels(engine, values):
    out = []
    for value in values:
        alert = engine.evaluate(r(value))
        out.append(alert.level if alert else None)
    return out


def test_no_alert_below_threshold():
    assert levels(AlertEngine(RULES), [10, 20, 34.9]) == [None, None, None]


def test_escalation_and_recovery():
    engine = AlertEngine(RULES, hysteresis=0.0)
    assert levels(engine, [10, 36, 40, 60, 60, 30]) == [
        None, "warning", None, "critical", None, "recovered"]


def test_hysteresis_prevents_flapping():
    engine = AlertEngine(RULES, hysteresis=0.1)
    # 34 > 35*0.9=31.5 -> ще warning; 31 < 31.5 -> повернення до норми
    assert levels(engine, [36, 34, 36, 31]) == ["warning", None, None, "recovered"]


def test_critical_to_warning_needs_hysteresis():
    engine = AlertEngine(RULES, hysteresis=0.1)
    # 54 > 55*0.9=49.5 -> лишається critical; 45 < 49.5 -> знижується до warning
    assert levels(engine, [60, 54, 45]) == ["critical", None, "warning"]


def test_state_is_per_device_and_metric():
    engine = AlertEngine(RULES)
    assert engine.evaluate(r(60, device="a")).level == "critical"
    assert engine.evaluate(r(60, device="b")).level == "critical"
    assert engine.evaluate(r(60, device="a")) is None


def test_unknown_metric_is_ignored():
    assert AlertEngine(RULES).evaluate(r(5000, metric="co2")) is None


def test_alert_message_contains_value_and_unit():
    alert = AlertEngine(RULES).evaluate(r(60))
    assert "pm25=60" in alert.message and "ug/m3" in alert.message


@pytest.mark.parametrize("hyst", [-0.1, 1.0])
def test_bad_hysteresis(hyst):
    with pytest.raises(ValueError):
        AlertEngine(RULES, hysteresis=hyst)


def test_bad_rule():
    with pytest.raises(ValueError):
        AlertEngine((AlertRule("pm25", 60, 50),))
