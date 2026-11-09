"""REST API вузла (стандартна бібліотека, лише читання) + мінімальний дашборд."""
from __future__ import annotations

import json
import logging
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, cast
from urllib.parse import parse_qs, urlsplit

from .aqi import category, overall_aqi
from .storage import Storage

LOG = logging.getLogger(__name__)

DASHBOARD = """<!doctype html>
<html lang="uk"><head><meta charset="utf-8"><title>AirEdge</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,sans-serif;margin:1.5rem;background:#f5f6f8;color:#1c1e21}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1rem}
.card{background:#fff;border-radius:10px;padding:1rem;box-shadow:0 1px 3px #0002}
.aqi{font-size:1.6rem;font-weight:700}
td{padding:.1rem .6rem .1rem 0}
</style></head><body>
<h1>AirEdge — якість повітря</h1><div id="grid" class="grid">Завантаження…</div>
<script>
async function tick(){
  try{
    const data = await (await fetch('/latest')).json();
    const grid = document.getElementById('grid'); grid.textContent='';
    for(const d of data.devices){
      const card = document.createElement('div'); card.className='card';
      const h = document.createElement('h3'); h.textContent = d.device; card.append(h);
      const a = document.createElement('div'); a.className='aqi';
      a.textContent = d.aqi ? 'AQI ' + d.aqi.value + ' — ' + d.aqi.category : 'AQI: н/д';
      card.append(a);
      const t = document.createElement('table');
      for(const [m,v] of Object.entries(d.metrics)){
        const r = t.insertRow(); r.insertCell().textContent = m;
        r.insertCell().textContent = v.value + ' ' + v.unit;
      }
      card.append(t); grid.append(card);
    }
    if(!data.devices.length) grid.textContent='Даних ще немає';
  }catch(e){ document.getElementById('grid').textContent='Немає зв\\'язку з вузлом'; }
}
tick(); setInterval(tick, 2000);
</script></body></html>
"""


class HttpError(Exception):
    """Помилка з HTTP-статусом."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class AirServer(ThreadingHTTPServer):
    """HTTP-сервер, що тримає посилання на сховище."""

    daemon_threads = True

    def __init__(self, address, handler, storage: Storage, stats_fn: Callable[[], dict] | None):
        super().__init__(address, handler)
        self.storage = storage
        self.stats_fn = stats_fn


def _one(query: dict, name: str, required: bool = False) -> str | None:
    values = query.get(name)
    if not values:
        if required:
            raise HttpError(400, f"обов'язковий параметр: {name}")
        return None
    return values[0]


def _number(query: dict, name: str, default: float | None = None) -> float | None:
    raw = _one(query, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise HttpError(400, f"параметр {name} має бути числом") from exc
    if not math.isfinite(value):
        raise HttpError(400, f"параметр {name} має бути скінченним")
    return value


def _limit(query: dict, default: int = 100) -> int:
    value = _number(query, "limit", default)
    if value is None or not 1 <= value <= 10000 or int(value) != value:
        raise HttpError(400, "limit має бути цілим у [1, 10000]")
    return int(value)


class Handler(BaseHTTPRequestHandler):
    """Маршрутизація GET-запитів."""

    server_version = "AirEdge/1.0"

    @property
    def _srv(self) -> AirServer:
        return cast(AirServer, self.server)

    def log_message(self, *args):  # тихий режим; журнал веде logging
        LOG.debug("http: %s", args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self):  # pylint: disable=invalid-name
        """Обробка GET."""
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/":
                self._send(200, DASHBOARD.encode("utf-8"), "text/html; charset=utf-8")
                return
            routes = {
                "/health": self._health,
                "/devices": self._devices,
                "/latest": self._latest,
                "/readings": self._readings,
                "/aggregate": self._aggregate,
                "/alerts": self._alerts,
                "/stats": self._stats,
            }
            route = routes.get(url.path)
            if route is None:
                raise HttpError(404, "не знайдено")
            self._json(200, route(query))
        except HttpError as err:
            self._json(err.status, {"error": err.message})
        except ValueError as err:
            self._json(400, {"error": str(err)})

    # --- маршрути --------------------------------------------------------
    def _health(self, _query):
        return {"status": "ok"}

    def _devices(self, _query):
        return {"devices": self._srv.storage.list_devices()}

    def _latest(self, query):
        rows = self._srv.storage.latest(_one(query, "device"))
        devices: dict[str, dict] = {}
        for row in rows:
            entry = devices.setdefault(row["device"], {"device": row["device"], "metrics": {}})
            entry["metrics"][row["metric"]] = {
                "value": row["value"], "unit": row["unit"], "ts": row["ts"]
            }
        for entry in devices.values():
            values = {m: v["value"] for m, v in entry["metrics"].items()}
            result = overall_aqi(values)
            entry["aqi"] = (
                {"value": result[0], "category": category(result[0]), "dominant": result[1]}
                if result
                else None
            )
        return {"devices": list(devices.values())}

    def _readings(self, query):
        return {
            "readings": self._srv.storage.query_readings(
                _one(query, "device", True),
                _one(query, "metric", True),
                _number(query, "since"),
                _number(query, "until"),
                _limit(query),
            )
        }

    def _aggregate(self, query):
        return {
            "buckets": self._srv.storage.aggregate(
                _one(query, "device", True),
                _one(query, "metric", True),
                _number(query, "bucket", 60.0),
                _number(query, "since"),
                _number(query, "until"),
            )
        }

    def _alerts(self, query):
        return {"alerts": self._srv.storage.recent_alerts(_limit(query, 50))}

    def _stats(self, _query):
        stats_fn = self._srv.stats_fn
        return stats_fn() if stats_fn else {"storage": self._srv.storage.stats()}


def make_server(
    storage: Storage,
    stats_fn: Callable[[], dict] | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> AirServer:
    """Створює (але не запускає) HTTP-сервер. port=0 — вільний порт."""
    return AirServer((host, port), Handler, storage, stats_fn)
