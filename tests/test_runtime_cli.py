import json
import time

import pytest

from airedge.cli import build_parser, main
from airedge.models import Reading as Reading_
from airedge.runtime import EdgeNode, NodeConfig
from airedge.sensors import FaultProfile, Sensor, SensorError, SimulatedSensor
from airedge.storage import Storage
from airedge.uplink import FileUplink, Uplink, UplinkError


class DownUplink(Uplink):
    def send(self, batch):
        raise UplinkError("offline")


class BrokenSensor(Sensor):
    device_id = "broken"

    def read(self):
        raise RuntimeError("driver bug")


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_node_collects_and_delivers(tmp_path):
    storage = Storage(":memory:")
    cloud = tmp_path / "cloud.jsonl"
    sensors = [SimulatedSensor(f"s{i}", seed=i) for i in range(3)]
    node = EdgeNode(storage, sensors, FileUplink(str(cloud)), NodeConfig(interval=0.02, sync_interval=0.05))
    node.start()
    assert wait_for(lambda: storage.stats()["readings"] >= 30)
    node.stop()
    stats = storage.stats()
    assert stats["devices"] == 3
    assert stats["outbox_pending"] == 0  # flush при зупинці
    assert len(cloud.read_text().splitlines()) == stats["readings"]
    assert node.stats()["queue_backlog"] == 0


def test_node_survives_sensor_faults():
    storage = Storage(":memory:")
    faulty = SimulatedSensor("f1", seed=1, faults=FaultProfile(dropout_rate=0.5, glitch_rate=0.3))
    node = EdgeNode(storage, [faulty, BrokenSensor()], DownUplink(), NodeConfig(interval=0.01, sync_interval=0.02))
    node.start()
    assert wait_for(lambda: node.counters.snapshot().get("sensor_errors", 0) >= 5)
    assert wait_for(lambda: node.counters.snapshot().get("sync_failures", 0) >= 1)
    node.stop()
    counters = node.counters.snapshot()
    assert counters["sensor_errors"] >= 5
    assert storage.stats()["outbox_pending"] == storage.stats()["readings"]  # хмара лежить — дані в буфері


def test_node_recovers_after_outage(tmp_path):
    class Switchable(Uplink):
        def __init__(self, path):
            self.up = False
            self.inner = FileUplink(path)

        def send(self, batch):
            if not self.up:
                raise UplinkError("offline")
            self.inner.send(batch)

    storage = Storage(":memory:")
    uplink = Switchable(str(tmp_path / "c.jsonl"))
    node = EdgeNode(storage, [SimulatedSensor("s", seed=1)], uplink, NodeConfig(interval=0.01, sync_interval=0.02))
    node.start()
    assert wait_for(lambda: storage.stats()["outbox_pending"] >= 5)
    uplink.up = True
    assert wait_for(lambda: storage.stats()["outbox_sent"] >= 5)
    node.stop()
    assert storage.stats()["outbox_pending"] == 0


def test_flush_stops_on_uplink_error():
    storage = Storage(":memory:")
    node = EdgeNode(storage, [], DownUplink())
    node.pipeline.process(SimulatedSensor("s", seed=1, clock=time.time).read()[0])
    node.flush()  # не кидає виняток
    assert storage.stats()["outbox_pending"] == 1


@pytest.mark.parametrize(
    "kw", [{"interval": 0}, {"sync_interval": -1}, {"batch_size": 0}, {"queue_size": 0}])
def test_node_config_validation(kw):
    with pytest.raises(ValueError):
        NodeConfig(**kw)


# ---------- CLI ----------
def test_cli_run_and_report(tmp_path, capsys):
    db = str(tmp_path / "a.db")
    cloud = str(tmp_path / "c.jsonl")
    code = main(["run", "--db", db, "--sensors", "2", "--interval", "0.05", "--duration", "0.6",
                 "--port", "0", "--seed", "1", "--cloud-file", cloud, "--uplink-failure-rate", "0",
                 "--sync-interval", "0.1", "--log-file", str(tmp_path / "x.log")])
    assert code == 0
    assert "Підсумок" in capsys.readouterr().out
    assert (tmp_path / "x.log").exists()
    first = json.loads((tmp_path / "c.jsonl").read_text().splitlines()[0])
    assert set(first) == {"device", "metric", "value", "ts"}

    assert main(["report", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "sensor-01" in out and "AQI" in out


def test_cli_run_without_api(tmp_path):
    code = main(["run", "--db", ":memory:", "--sensors", "1", "--interval", "0.05",
                 "--duration", "0.2", "--port", "-1", "--cloud-file", str(tmp_path / "c.jsonl")])
    assert code == 0


def test_cli_report_empty_db(tmp_path, capsys):
    assert main(["report", "--db", str(tmp_path / "empty.db")]) == 0
    assert "порожня" in capsys.readouterr().out


def test_cli_report_without_aqi_metrics(tmp_path, capsys):
    from airedge.models import Reading
    db = str(tmp_path / "co2.db")
    store = Storage(db)
    store.save_reading(Reading("x", "co2", 500.0, 10.0))
    store.close()
    assert main(["report", "--db", db]) == 0
    assert "AQI н/д" in capsys.readouterr().out


def test_cli_negative_duration(capsys):
    assert main(["run", "--duration", "-1"]) == 2
    assert "duration" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [["run", "--interval", "0"], ["run", "--sensors", "0"], ["run", "--sensors", "101"],
     ["run", "--fault-rate", "2"], ["run", "--sync-interval", "-1"], ["bogus"], []],
)
def test_cli_rejects_bad_arguments(args):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(args)
    assert exc.value.code == 2


def test_wait_handles_keyboard_interrupt(monkeypatch):
    from airedge import cli

    def boom(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", boom)
    cli._wait(5)  # pylint: disable=protected-access  # не повинно кидати виняток


def test_sync_step_catches_up_backlog_in_one_cycle(tmp_path):
    storage = Storage(":memory:")
    for i in range(250):
        storage.save_reading(Reading_("s1", "pm25", 10.0, 1000.0 + i))
    node = EdgeNode(storage, [], FileUplink(str(tmp_path / "c.jsonl")), NodeConfig(batch_size=100))
    assert node._sync_step(3) == 0  # pylint: disable=protected-access
    assert storage.stats()["outbox_pending"] == 0
    assert node.counters.snapshot()["synced"] == 250


def test_sync_step_failure_increments_failures():
    storage = Storage(":memory:")
    storage.save_reading(Reading_("s1", "pm25", 10.0, 1000.0))
    node = EdgeNode(storage, [], DownUplink())
    assert node._sync_step(2) == 3  # pylint: disable=protected-access
