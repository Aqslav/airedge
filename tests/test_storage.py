import sqlite3

import pytest

from airedge.models import Alert, Reading
from airedge.storage import Storage


def rd(ts=100.0, value=10.0, device="s1", metric="pm25"):
    return Reading(device, metric, value, ts)


def test_save_and_query(storage):
    assert storage.save_reading(rd(100.0, 1.0))
    assert storage.save_reading(rd(101.0, 2.0))
    rows = storage.query_readings("s1", "pm25")
    assert [x["value"] for x in rows] == [2.0, 1.0]  # найновіші першими


def test_duplicate_is_ignored(storage):
    assert storage.save_reading(rd(100.0))
    assert not storage.save_reading(rd(100.0, value=99.0))
    assert storage.stats()["readings"] == 1
    assert storage.stats()["outbox_pending"] == 1  # дубль не потрапив в outbox


def test_unknown_metric_rolls_back(storage):
    with pytest.raises(ValueError):
        storage.save_reading(Reading("s1", "radon", 1.0, 100.0))
    assert storage.stats()["devices"] == 0  # транзакція відкотилась разом із пристроєм


def test_check_constraint_is_not_swallowed(storage):
    with pytest.raises(sqlite3.IntegrityError):
        storage.save_reading(rd(ts=-1.0))
    assert storage.stats()["readings"] == 0


def test_foreign_keys_enforced(storage):
    with pytest.raises(sqlite3.IntegrityError):
        with storage._conn:  # pylint: disable=protected-access
            storage._conn.execute(  # pylint: disable=protected-access
                "INSERT INTO readings(device_id, metric_id, value, ts) VALUES (999, 1, 1, 1)")


def test_sql_injection_is_harmless(storage):
    storage.save_reading(rd())
    evil = "s1' OR '1'='1"
    assert storage.query_readings(evil, "pm25") == []
    assert storage.query_readings("s1", "pm25; DROP TABLE readings") == []
    assert storage.stats()["readings"] == 1


def test_latest_per_device_and_metric(storage):
    storage.save_reading(rd(100.0, 1.0))
    storage.save_reading(rd(200.0, 2.0))
    storage.save_reading(rd(150.0, 5.0, device="s2"))
    latest = storage.latest()
    assert {(x["device"], x["value"]) for x in latest} == {("s1", 2.0), ("s2", 5.0)}
    assert [x["device"] for x in storage.latest("s2")] == ["s2"]


def test_query_time_range_and_limit(storage):
    for ts in range(1, 11):
        storage.save_reading(rd(float(ts), float(ts)))
    rows = storage.query_readings("s1", "pm25", since=3, until=6)
    assert sorted(x["ts"] for x in rows) == [3.0, 4.0, 5.0, 6.0]
    assert len(storage.query_readings("s1", "pm25", limit=2)) == 2


@pytest.mark.parametrize("limit", [0, -1, 10_001, "5", True])
def test_bad_limit(storage, limit):
    with pytest.raises(ValueError):
        storage.query_readings("s1", "pm25", limit=limit)


def test_aggregate_buckets(storage):
    for ts, value in [(0.5, 10), (30, 20), (61, 30), (100, 50)]:
        storage.save_reading(rd(float(ts), float(value)))
    buckets = storage.aggregate("s1", "pm25", bucket_s=60)
    assert [(b["bucket"], b["avg"], b["min"], b["max"], b["count"]) for b in buckets] == [
        (0, 15.0, 10.0, 20.0, 2), (60, 40.0, 30.0, 50.0, 2)]


def test_aggregate_bad_bucket(storage):
    with pytest.raises(ValueError):
        storage.aggregate("s1", "pm25", bucket_s=0)


def test_alerts_roundtrip(storage):
    storage.save_alert(Alert("s1", "pm25", "warning", 40.0, 5.0, "msg"))
    storage.save_alert(Alert("s1", "pm25", "recovered", 20.0, 9.0, "msg2"))
    alerts = storage.recent_alerts()
    assert [a["level"] for a in alerts] == ["recovered", "warning"]


def test_alert_level_constraint(storage):
    with pytest.raises(sqlite3.IntegrityError):
        storage.save_alert(Alert("s1", "pm25", "panic", 1.0, 1.0, "x"))


def test_outbox_lifecycle(storage):
    for ts in (1.0, 2.0, 3.0):
        storage.save_reading(rd(ts))
    batch = storage.pending_batch(2)
    assert len(batch) == 2 and batch[0]["ts"] == 1.0
    ids = [b["outbox_id"] for b in batch]
    storage.mark_failed(ids)
    row = storage._conn.execute("SELECT attempts FROM outbox WHERE id=?", (ids[0],)).fetchone()  # pylint: disable=protected-access
    assert row["attempts"] == 1
    storage.mark_sent(ids, 50.0)
    assert storage.stats()["outbox_sent"] == 2
    assert len(storage.pending_batch()) == 1


def test_mark_sent_default_time(storage):
    storage.save_reading(rd())
    storage.mark_sent([storage.pending_batch()[0]["outbox_id"]])
    assert storage.stats()["outbox_sent"] == 1


def test_purge_only_sent(storage):
    storage.save_reading(rd(1.0))
    storage.save_reading(rd(2.0))
    first = storage.pending_batch(1)[0]["outbox_id"]
    storage.mark_sent([first], 10.0)
    assert storage.purge_sent(before_ts=100.0) == 1
    assert storage.stats()["readings"] == 1  # непідтверджене не видалено
    assert storage.stats()["outbox_pending"] == 1


def test_list_devices(storage):
    storage.save_reading(rd(device="b"))
    storage.save_reading(rd(device="a"))
    assert [d["name"] for d in storage.list_devices()] == ["a", "b"]


def test_persistence_on_disk(tmp_path):
    path = str(tmp_path / "x.db")
    first = Storage(path)
    first.save_reading(rd())
    first.close()
    second = Storage(path)
    assert second.stats()["readings"] == 1
    second.close()
