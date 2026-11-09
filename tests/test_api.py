import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from airedge.api import make_server
from airedge.models import Alert, Reading


@pytest.fixture
def server(storage):
    storage.save_reading(Reading("s1", "pm25", 12.0, 100.0))
    storage.save_reading(Reading("s1", "pm25", 20.0, 160.0))
    storage.save_reading(Reading("s1", "pm10", 30.0, 160.0))
    storage.save_reading(Reading("s1", "co2", 500.0, 160.0))
    storage.save_alert(Alert("s1", "pm25", "warning", 40.0, 160.0, "m"))
    srv = make_server(storage, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            return resp.status, resp.headers.get_content_type(), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get_content_type(), err.read()


def get_json(base, path):
    status, _, body = get(base, path)
    return status, json.loads(body)


def test_health(server):
    assert get_json(server, "/health") == (200, {"status": "ok"})


def test_dashboard_html(server):
    status, ctype, body = get(server, "/")
    assert status == 200 and ctype == "text/html" and b"AirEdge" in body


def test_devices(server):
    assert get_json(server, "/devices")[1]["devices"][0]["name"] == "s1"


def test_latest_contains_aqi(server):
    _, data = get_json(server, "/latest")
    device = data["devices"][0]
    assert device["metrics"]["pm25"]["value"] == 20.0
    assert device["aqi"]["dominant"] in ("pm25", "pm10")
    assert device["aqi"]["category"]


def test_latest_without_aqi_metrics(storage):
    storage.save_reading(Reading("s9", "co2", 500.0, 10.0))
    srv = make_server(storage, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _, data = get_json(f"http://127.0.0.1:{srv.server_address[1]}", "/latest?device=s9")
        assert data["devices"][0]["aqi"] is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_readings_ok(server):
    _, data = get_json(server, "/readings?device=s1&metric=pm25&limit=1")
    assert data["readings"] == [{"ts": 160.0, "value": 20.0}]


def test_readings_time_filter(server):
    _, data = get_json(server, "/readings?device=s1&metric=pm25&since=150")
    assert len(data["readings"]) == 1


def test_aggregate(server):
    _, data = get_json(server, "/aggregate?device=s1&metric=pm25&bucket=60")
    assert sum(b["count"] for b in data["buckets"]) == 2


def test_alerts_and_stats(server):
    assert get_json(server, "/alerts")[1]["alerts"][0]["level"] == "warning"
    assert get_json(server, "/stats")[1]["storage"]["readings"] == 4


def test_stats_uses_custom_function(storage):
    srv = make_server(storage, stats_fn=lambda: {"custom": 1}, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert get_json(f"http://127.0.0.1:{srv.server_address[1]}", "/stats")[1] == {"custom": 1}
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize(
    "path",
    [
        "/readings",                                    # немає обов'язкових параметрів
        "/readings?device=s1",
        "/readings?device=s1&metric=pm25&limit=abc",
        "/readings?device=s1&metric=pm25&limit=0",
        "/readings?device=s1&metric=pm25&limit=1.5",
        "/readings?device=s1&metric=pm25&since=nan",
        "/aggregate?device=s1&metric=pm25&bucket=-5",
        "/aggregate?device=s1&metric=pm25&bucket=0",
    ],
)
def test_bad_requests_return_400(server, path):
    status, data = get_json(server, path)
    assert status == 400 and "error" in data


def test_unknown_route_404(server):
    assert get_json(server, "/nope")[0] == 404


def test_sql_injection_via_api(server):
    evil = urllib.parse.quote("s1' OR '1'='1")
    status, data = get_json(server, f"/readings?device={evil}&metric=pm25")
    assert status == 200 and data["readings"] == []
    assert get_json(server, "/stats")[1]["storage"]["readings"] == 4


def test_empty_database(storage):
    srv = make_server(storage, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert get_json(base, "/latest") == (200, {"devices": []})
        assert get_json(base, "/alerts") == (200, {"alerts": []})
    finally:
        srv.shutdown()
        srv.server_close()
