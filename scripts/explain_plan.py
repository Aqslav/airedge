"""Показує план виконання основного запиту (доказ використання індексів, критерій C5).

Запуск з кореня репозиторію: python scripts/explain_plan.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from airedge.storage import Storage  # noqa: E402  pylint: disable=wrong-import-position

QUERY = (
    "SELECT r.ts, r.value FROM readings r "
    "JOIN devices d ON d.id = r.device_id JOIN metrics m ON m.id = r.metric_id "
    "WHERE d.name = 's1' AND m.code = 'pm25' ORDER BY r.ts DESC LIMIT 10"
)

if __name__ == "__main__":
    store = Storage(":memory:")
    # pylint: disable-next=protected-access
    for row in store._conn.execute("EXPLAIN QUERY PLAN " + QUERY):
        print(row[3])
