"""Приймальні тест-кейси TC-01..TC-11 (див. docs/TEST_CASES.md). Наскрізні, через публічні інтерфейси."""
import json
import threading
import time
import urllib.request

from airedge.alerts import AlertEngine, AlertRule
from airedge.api import make_server
from airedge.cli import main
from airedge.models import METRICS, Reading
from airedge.runtime import EdgeNode, NodeConfig
from airedge.sensors import SimulatedSensor
from airedge.storage import Storage
from airedge.uplink import FileUplink, Uplink, UplinkError

FAST = NodeConfig(interval=0.02, sync_interval=0.03)


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def http_json(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return json.loads(resp.read())


def run_node(storage, uplink, sensors=3, config=FAST, min_readings=45):
    node = EdgeNode(storage, [SimulatedSensor(f"sensor-{i + 1:02d}", seed=i) for i in range(sensors)],
                    uplink, config)
    node.start()
    assert wait_for(lambda: storage.stats()["readings"] >= min_readings)
    return node


def test_tc01_all_sensors_deliver_data(tmp_path):
    storage = Storage(":memory:")
    node = run_node(storage, FileUplink(str(tmp_path / "c.jsonl")))
    node.stop()
    assert [d["name"] for d in storage.list_devices()] == ["sensor-01", "sensor-02", "sensor-03"]


def test_tc02_all_metrics_in_valid_ranges(tmp_path):
    storage = Storage(":memory:")
    node = run_node(storage, FileUplink(str(tmp_path / "c.jsonl")))
    node.stop()
    latest = storage.latest()
    assert {row["metric"] for row in latest} == set(METRICS)
    for row in latest:
        spec = METRICS[row["metric"]]
        assert spec.min_value <= row["value"] <= spec.max_value


def test_tc03_aqi_known_values():
    from airedge.aqi import compute_aqi
    assert compute_aqi("pm25", 35.4) == 100
    assert compute_aqi("pm10", 154) == 100


def test_tc04_rest_latest_and_aqi(storage):
    storage.save_reading(Reading("sensor-01", "pm25", 35.4, 10.0))
    srv = make_server(storage, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        data = http_json(f"http://127.0.0.1:{srv.server_address[1]}", "/latest")
        assert data["devices"][0]["aqi"]["value"] == 100
    finally:
        srv.shutdown()
        srv.server_close()


def test_tc05_alert_and_recovery(storage):
    node = EdgeNode(storage, [], FileUplink("unused"), NodeConfig(
        alert_engine=AlertEngine((AlertRule("pm25", 35.0, 55.0),), hysteresis=0.0),
        clock=lambda: 1000.0))
    for ts, value in [(1.0, 10), (2.0, 40), (3.0, 60), (4.0, 10)]:
        node.pipeline.process(Reading("s1", "pm25", float(value), float(ts)))
    levels = [a["level"] for a in reversed(storage.recent_alerts())]
    assert levels == ["warning", "critical", "recovered"]


def test_tc06_invalid_readings_rejected(storage):
    node = EdgeNode(storage, [], FileUplink("unused"), NodeConfig(clock=lambda: 1000.0))
    for value in (float("nan"), -1.0, 5000.0):
        node.pipeline.process(Reading("s1", "pm25", value, 10.0))
    assert storage.stats()["readings"] == 0
    assert node.counters.snapshot()["rejected"] == 3


def test_tc07_cloud_outage_no_loss_no_duplicates(tmp_path):
    class Switch(Uplink):
        def __init__(self, path):
            self.up, self.inner = False, FileUplink(path)

        def send(self, batch):
            if not self.up:
                raise UplinkError("offline")
            self.inner.send(batch)

    cloud = tmp_path / "c.jsonl"
    switch = Switch(str(cloud))
    storage = Storage(":memory:")
    node = run_node(storage, switch, min_readings=30)
    switch.up = True
    assert wait_for(lambda: storage.stats()["outbox_sent"] >= 30)
    node.stop()
    rows = [json.loads(line) for line in cloud.read_text().splitlines()]
    keys = {(r["device"], r["metric"], r["ts"]) for r in rows}
    assert len(rows) == len(keys) == storage.stats()["readings"]


def test_tc08_data_survives_restart(tmp_path):
    path = str(tmp_path / "n.db")
    storage = Storage(path)
    node = run_node(storage, FileUplink(str(tmp_path / "c.jsonl")), sensors=1, min_readings=10)
    node.stop()
    count = storage.stats()["readings"]
    storage.close()
    reopened = Storage(path)
    assert reopened.stats()["readings"] == count
    reopened.close()


def test_tc09_dashboard_available(storage):
    srv = make_server(storage, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/", timeout=5) as resp:
            assert resp.status == 200 and b"AirEdge" in resp.read()
    finally:
        srv.shutdown()
        srv.server_close()


def test_tc10_cli_report(tmp_path, capsys):
    db = str(tmp_path / "r.db")
    store = Storage(db)
    store.save_reading(Reading("sensor-01", "pm25", 12.0, 5.0))
    store.close()
    assert main(["report", "--db", db]) == 0
    assert "sensor-01: AQI" in capsys.readouterr().out


def test_tc11_minute_aggregation(storage):
    for ts, value in [(10.0, 10.0), (20.0, 20.0), (70.0, 40.0)]:
        storage.save_reading(Reading("s1", "pm25", value, ts))
    buckets = storage.aggregate("s1", "pm25", 60)
    assert [(b["avg"], b["count"]) for b in buckets] == [(15.0, 2), (40.0, 1)]
