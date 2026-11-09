import json
import random

import pytest

from airedge.alerts import AlertEngine, AlertRule
from airedge.models import Reading
from airedge.pipeline import Counters, Pipeline
from airedge.uplink import FileUplink, Uplink, UplinkError, backoff_delay, sync_once

NOW = 1_700_000_000.0


def rd(ts=NOW, value=10.0, metric="pm25", device="s1"):
    return Reading(device, metric, value, ts)


class FlakyUplink(Uplink):
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.received = []

    def send(self, batch):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise UplinkError("offline")
        self.received.extend(batch)


# ---------- Pipeline ----------
def make_pipeline(storage, queue_size=10):
    engine = AlertEngine((AlertRule("pm25", 35.0, 55.0),))
    return Pipeline(storage, engine, Counters(), queue_size, clock=lambda: NOW)


def test_process_valid(storage):
    pipe = make_pipeline(storage)
    assert pipe.process(rd())
    assert pipe.counters.snapshot()["accepted"] == 1


def test_process_rejects_invalid(storage):
    pipe = make_pipeline(storage)
    assert not pipe.process(rd(value=float("nan")))
    assert not pipe.process(rd(value=-5))
    assert pipe.counters.snapshot()["rejected"] == 2
    assert storage.stats()["readings"] == 0


def test_process_duplicate(storage):
    pipe = make_pipeline(storage)
    pipe.process(rd())
    assert not pipe.process(rd())
    assert pipe.counters.snapshot()["duplicates"] == 1


def test_process_generates_and_stores_alert(storage):
    pipe = make_pipeline(storage)
    pipe.process(rd(ts=NOW, value=60))
    assert pipe.counters.snapshot()["alerts"] == 1
    assert storage.recent_alerts()[0]["level"] == "critical"


def test_queue_overflow_drops_and_counts(storage):
    pipe = make_pipeline(storage, queue_size=2)
    results = [pipe.submit(rd(ts=NOW + i)) for i in range(5)]
    assert results == [True, True, False, False, False]
    assert pipe.counters.snapshot()["dropped"] == 3
    assert pipe.backlog() == 2


def test_drain(storage):
    pipe = make_pipeline(storage)
    assert not pipe.drain(timeout=0.01)  # порожня черга
    pipe.submit(rd())
    assert pipe.drain(timeout=0.01)
    assert storage.stats()["readings"] == 1


def test_storage_error_does_not_crash(storage):
    pipe = make_pipeline(storage)
    storage.close()  # ламаємо сховище
    assert not pipe.process(rd())
    assert pipe.counters.snapshot()["storage_errors"] == 1


# ---------- Uplink ----------
def fill(storage, n):
    for i in range(n):
        storage.save_reading(rd(ts=NOW + i))


def test_sync_sends_and_marks(storage):
    fill(storage, 3)
    up = FlakyUplink(0)
    assert sync_once(storage, up, batch_size=2, clock=lambda: 5.0) == 2
    assert len(up.received) == 2 and "outbox_id" not in up.received[0]
    assert storage.stats()["outbox_pending"] == 1


def test_sync_nothing_to_send(storage):
    assert sync_once(storage, FlakyUplink(0)) == 0


def test_no_data_loss_when_cloud_down(storage):
    fill(storage, 5)
    up = FlakyUplink(fail_times=2)
    for _ in range(2):
        with pytest.raises(UplinkError):
            sync_once(storage, up)
    assert storage.stats()["outbox_pending"] == 5  # нічого не втрачено
    assert sync_once(storage, up) == 5            # після відновлення — доставлено все
    assert len(up.received) == 5
    assert storage.stats()["outbox_pending"] == 0


def test_file_uplink_writes_jsonl(tmp_path):
    path = tmp_path / "cloud.jsonl"
    FileUplink(str(path)).send([{"a": 1}, {"a": 2}])
    assert [json.loads(line) for line in path.read_text().splitlines()] == [{"a": 1}, {"a": 2}]


def test_file_uplink_simulated_failure(tmp_path):
    with pytest.raises(UplinkError):
        FileUplink(str(tmp_path / "x"), failure_rate=1.0, rng=random.Random(1)).send([{"a": 1}])


def test_file_uplink_os_error(tmp_path):
    with pytest.raises(UplinkError):
        FileUplink(str(tmp_path / "no_dir" / "x.jsonl")).send([{"a": 1}])


def test_file_uplink_bad_rate():
    with pytest.raises(ValueError):
        FileUplink("x", failure_rate=2)


@pytest.mark.parametrize(
    "failures,expected", [(0, 1.0), (1, 2.0), (2, 4.0), (5, 30.0), (1000, 30.0)])
def test_backoff_delay(failures, expected):
    assert backoff_delay(failures, 1.0, 30.0) == expected


@pytest.mark.parametrize("args", [(-1, 1, 10), (0, 0, 10), (0, 5, 1)])
def test_backoff_bad_args(args):
    with pytest.raises(ValueError):
        backoff_delay(*args)
