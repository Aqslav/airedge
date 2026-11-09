"""Командний рядок: `python -m airedge run|report`."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from .aqi import category, overall_aqi
from .api import make_server
from .runtime import EdgeNode, NodeConfig
from .sensors import FaultProfile, SimulatedSensor
from .storage import Storage
from .uplink import FileUplink

LOG = logging.getLogger("airedge")


def _positive(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("значення має бути > 0")
    return value


def _rate(text: str) -> float:
    value = float(text)
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError("значення має бути в [0, 1]")
    return value


def _count(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError("кількість датчиків має бути в [1, 100]")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Описує аргументи командного рядка."""
    parser = argparse.ArgumentParser(prog="airedge", description="Edge-платформа моніторингу повітря")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="запустити вузол із симульованими датчиками")
    run.add_argument("--db", default="airedge.db", help="файл SQLite (:memory: — у пам'яті)")
    run.add_argument("--sensors", type=_count, default=3, help="кількість датчиків-муляжів")
    run.add_argument("--interval", type=_positive, default=1.0, help="період опитування, с")
    run.add_argument("--duration", type=float, default=30.0, help="тривалість, с (0 — до Ctrl+C)")
    run.add_argument("--seed", type=int, default=None, help="seed для відтворюваних даних")
    run.add_argument("--fault-rate", type=_rate, default=0.05, help="ймовірність збою датчика")
    run.add_argument("--episode-rate", type=_rate, default=0.02, help="ймовірність забруднення")
    run.add_argument("--cloud-file", default="cloud_sink.jsonl", help="файл-імітація хмари")
    run.add_argument("--uplink-failure-rate", type=_rate, default=0.2, help="імовірність обриву зв'язку")
    run.add_argument("--sync-interval", type=_positive, default=2.0, help="період синхронізації, с")
    run.add_argument("--port", type=int, default=8080, help="порт API (0 — вільний, -1 — вимкнено)")
    run.add_argument("--log-file", default=None, help="додатково писати журнал у файл")

    report = sub.add_parser("report", help="показати підсумок із бази")
    report.add_argument("--db", default="airedge.db")
    return parser


def _setup_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _wait(duration: float) -> None:
    """Чекає duration секунд (0 — безкінечно); Ctrl+C завершує очікування."""
    try:
        if duration == 0:
            threading.Event().wait()
        else:
            time.sleep(duration)
    except KeyboardInterrupt:
        LOG.info("отримано Ctrl+C, зупиняємось…")


def cmd_run(args: argparse.Namespace) -> int:
    """Запускає вузол."""
    if args.duration < 0:
        print("помилка: --duration не може бути від'ємним", file=sys.stderr)
        return 2
    _setup_logging(args.log_file)
    storage = Storage(args.db)
    faults = FaultProfile(args.fault_rate, args.fault_rate / 2, args.episode_rate)
    sensors = [
        SimulatedSensor(
            f"sensor-{i + 1:02d}",
            seed=None if args.seed is None else args.seed + i,
            faults=faults,
        )
        for i in range(args.sensors)
    ]
    node = EdgeNode(
        storage,
        sensors,
        FileUplink(args.cloud_file, args.uplink_failure_rate),
        NodeConfig(interval=args.interval, sync_interval=args.sync_interval),
    )
    server = None
    if args.port >= 0:
        server = make_server(storage, node.stats, port=args.port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        LOG.info("API та дашборд: http://127.0.0.1:%d/", server.server_address[1])

    node.start()
    LOG.info("вузол запущено: %d датчиків, db=%s", args.sensors, args.db)
    _wait(args.duration)
    node.stop()
    if server is not None:
        server.shutdown()
        server.server_close()
    print("Підсумок:", node.stats())
    storage.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Друкує останні значення, AQI та статистику."""
    storage = Storage(args.db)
    latest = storage.latest()
    if not latest:
        print("База порожня.")
        storage.close()
        return 0
    by_device: dict[str, dict[str, float]] = {}
    for row in latest:
        by_device.setdefault(row["device"], {})[row["metric"]] = row["value"]
    for device, values in by_device.items():
        aqi = overall_aqi(values)
        aqi_text = f"AQI {aqi[0]} ({category(aqi[0])})" if aqi else "AQI н/д"
        print(f"{device}: {aqi_text}; " + ", ".join(f"{m}={v}" for m, v in values.items()))
    print("Статистика:", storage.stats())
    storage.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Точка входу."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return cmd_run(args) if args.command == "run" else cmd_report(args)
