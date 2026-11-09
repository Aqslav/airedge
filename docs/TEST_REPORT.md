# Звіт з тестування (критерії C0–C5)

Проєкт: **AirEdge**, автор: Антон Мудрий. Усі команди — з кореня репозиторію.

---

## C0. Демонстрація на кількох ОС

| ОС | Як показано | Підтвердження |
|---|---|---|
| Linux (Ubuntu) | `python -m airedge run --duration 20` | скріншот / CI-job |
| Windows | те саме | скріншот / CI-job |
| macOS | CI-job (якщо немає фізичного Mac) | зелений workflow |

CI (`.github/workflows/ci.yml`) запускає тести й лінтер у матриці **ubuntu / windows / macos × Python 3.10 / 3.12**.
Для захисту: одна ОС наживо + зелена матриця CI на інших (або друга ОС у віртуальній машині).
Код не містить ОС-специфічних викликів: лише `sqlite3`, `threading`, `http.server`; файли відкриваються з `encoding="utf-8"`.

---

## C1. Приймальні тест-кейси (фіксований список)

Усі кейси автоматизовані в `tests/test_acceptance.py` (функції `test_tcNN_*`) **і** відтворюються вручну.
Запуск: `pytest tests/test_acceptance.py -v`.

| ID | Опис | Кроки для відтворення (вручну) | Очікуваний результат | Автотест |
|---|---|---|---|---|
| TC-01 | Усі датчики віддають дані | `python -m airedge run --duration 10 --sensors 3`, потім `python -m airedge report` | у звіті 3 пристрої `sensor-01..03` | `test_tc01_all_sensors_deliver_data` |
| TC-02 | Усі 5 метрик у допустимих межах | під час роботи вузла: `curl localhost:8080/latest` | є pm25, pm10, co2, temperature, humidity; значення в діапазонах | `test_tc02_all_metrics_in_valid_ranges` |
| TC-03 | Правильний AQI | `pytest tests/test_aqi.py -v` | pm25=35.4 → 100, pm10=154 → 100 та всі брейкпоінти | `test_tc03_aqi_known_values` |
| TC-04 | REST `/latest` повертає AQI | `curl localhost:8080/latest` | JSON із `aqi.value` і `aqi.category` | `test_tc04_rest_latest_and_aqi` |
| TC-05 | Сповіщення та повернення до норми | `run --episode-rate 0.3 --duration 40`, потім `curl localhost:8080/alerts` | послідовність warning → critical → recovered | `test_tc05_alert_and_recovery` |
| TC-06 | Некоректні показання відхиляються | `run --fault-rate 0.4`; у журналі `показання відхилено`, у `/stats` `rejected > 0` | у БД немає NaN / від'ємних / викидів | `test_tc06_invalid_readings_rejected` |
| TC-07 | Обрив хмари: без втрат і дублікатів | `run --uplink-failure-rate 0.7 --duration 30`, потім повторний `run --uplink-failure-rate 0` | `outbox_pending` росте під час обриву, після відновлення → 0; у `cloud_sink.jsonl` немає дублікатів | `test_tc07_cloud_outage_no_loss_no_duplicates` |
| TC-08 | Дані переживають перезапуск | `run --duration 5`, потім `report` | дані на місці | `test_tc08_data_survives_restart` |
| TC-09 | Дашборд відкривається | відкрити `http://127.0.0.1:8080/` | картки пристроїв з AQI, оновлення кожні 2 с | `test_tc09_dashboard_available` |
| TC-10 | CLI-звіт | `python -m airedge report` | рядок `sensor-01: AQI ...` | `test_tc10_cli_report` |
| TC-11 | Агрегація по хвилинах | `curl "localhost:8080/aggregate?device=sensor-01&metric=pm25&bucket=60"` | avg / min / max / count по вікнах | `test_tc11_minute_aggregation` |

**Результат виконання:** 11 із 11 пройдено → частка = 1.0.
Для захисту додайте скріншоти виводу `pytest -v` і ручних кроків.

---

## C2. Крайні випадки та помилки

### П'ять крайніх випадків (формат «випадок → оброблено / ні»)

| # | Крайній випадок | Оброблено | Як саме | Доказ |
|---|---|---|---|---|
| 1 | Датчик перестав відповідати | **так** | `SensorError` перехоплюється, лічильник `sensor_errors`, потік живе далі | `test_dropout_raises_sensor_error`, `test_node_survives_sensor_faults` |
| 2 | Датчик повернув NaN / від'ємне / значення поза фізичним діапазоном | **так** | `validate()` відхиляє, лічильник `rejected`, у БД не потрапляє | `test_invalid_readings_rejected` (17 варіантів), TC-06 |
| 3 | Дубльоване показання (той самий пристрій / метрика / час) | **так** | `UNIQUE`-обмеження, `save_reading` повертає `False`, лічильник `duplicates`, outbox не дублюється | `test_duplicate_is_ignored` |
| 4 | Хмара недоступна | **так** | store-and-forward: outbox у БД, експоненційний backoff, після відновлення — доставка; при зупинці — `flush` | TC-07, `test_no_data_loss_when_cloud_down`, `test_backoff_delay` |
| 5 | Лавина даних (черга переповнена) | **так** | обмежена черга; зайве відкидається з лічильником `dropped`, вузол не падає | `test_queue_overflow_drops_and_counts` |

Додатково оброблено: порожня БД в API (`test_empty_database`), некоректні параметри API → `400`
(8 варіантів), SQL-ін'єкції (`test_sql_injection_*`), збій сховища під час обробки
(`test_storage_error_does_not_crash`), несподівана помилка драйвера датчика (`BrokenSensor`), межові
значення діапазонів і брейкпоінтів AQI.

### Відповідність п'яти пунктам рубрики C2

| Пункт рубрики | Виконано | Де |
|---|---|---|
| (1) валідація введення | так | `validation.py`, `api.py` (`_number`, `_limit`), `cli.py` (типи аргументів) |
| (2) порожні / некоректні дані | так | `test_empty_database`, `test_invalid_readings_rejected` |
| (3) винятки без аварійного завершення | так | `_sensor_loop`, `Pipeline.process`, `_sync_step`, `Handler.do_GET` |
| (4) межові значення | так | `test_boundary_values_are_valid`, параметризовані брейкпоінти AQI |
| (5) немає необроблених винятків у лог-файлі | так (показати наживо) | див. нижче |

**Перевірка п. 5** (стрес-прогін: 30% збоїв датчиків, 60% втрат зв'язку):

```bash
python -m airedge run --duration 15 --interval 0.2 --fault-rate 0.3 --uplink-failure-rate 0.6 \
       --episode-rate 0.1 --port -1 --log-file run.log
grep -c Traceback run.log
# Windows PowerShell: (Select-String -Path run.log -Pattern Traceback).Count
```

У тестовому прогоні (Linux) програма завершилась з кодом 0 і `Traceback` у журналі — 0, при цьому
збої оброблялись як `WARNING` (відхилені показання, збої датчиків, невдалі синхронізації).

---

## C3. Автотести та покриття

| Показник | Значення |
|---|---|
| Кількість автотестів | **173** |
| Покриття (рядки + гілки), `pytest-cov` | **99%** (поріг у CI — 80%) |
| CI ганяє тести | так: 3 ОС × 2 версії Python, `--cov-fail-under=80` |

Відтворення: `pytest --cov=airedge --cov-report=term-missing`
(або `python scripts/quality_report.py` → `reports/htmlcov/index.html`).
Показати: звіт інструмента та зелений прогін GitHub Actions. **Число береться лише зі звіту інструмента.**

---

## C4. Статичний аналіз

Інструмент: **pylint 3** (конфіг — `.pylintrc`).

| Показник | Значення |
|---|---|
| `pylint airedge` | **10.00 / 10**, 0 зауважень |
| Рядків коду (`airedge/`) | ≈ 1350 |
| Critical/major на 1000 рядків | **0** → рівень 1.0 |

Відтворення: `pylint airedge`. У коді лише обґрунтовані `# pylint: disable`:
`broad-exception-caught` у потоці датчика (потік не має вмирати від помилки драйвера) та
`invalid-name` для `do_GET` (ім'я задане `http.server`).

---

## C5. Цілісність і безпека роботи з даними (SQLite)

| Критерій | Виконано | Підтвердження |
|---|---|---|
| Нормалізація ≥ 3НФ | **так** | таблиці `devices`, `metrics`, `readings`, `alerts`, `outbox`; показання посилаються на пристрій і метрику за FK; одиниці виміру й діапазони зберігаються один раз у `metrics`; транзитивних залежностей немає (`storage.py`, `SCHEMA`) |
| PK / FK та обмеження цілісності | **так** | `PRIMARY KEY`, `FOREIGN KEY ... ON DELETE CASCADE`, `UNIQUE(device_id, metric_id, ts)`, `CHECK (ts > 0)`, `CHECK (level IN ...)`, `CHECK (min_value < max_value)`; `PRAGMA foreign_keys = ON`; тести `test_foreign_keys_enforced`, `test_alert_level_constraint` |
| Індекси на полях пошуку / JOIN | **так** | автоіндекс `UNIQUE(device_id, metric_id, ts)` (основний пошук), `idx_readings_ts`, `idx_alerts_ts`, частковий `idx_outbox_pending WHERE sent_at IS NULL`; докази: `python scripts/explain_plan.py` |
| Параметризовані запити | **так** | усі SQL у `storage.py` використовують `?`; тести `test_sql_injection_is_harmless`, `test_sql_injection_via_api` |
| Транзакції | **так** | `save_reading` атомарно пише `readings` + `outbox` (`with conn:`); `test_unknown_metric_rolls_back` показує відкат |

Результат `python scripts/explain_plan.py` (повного сканування таблиці немає, використовуються індекси):

```
SEARCH d USING COVERING INDEX sqlite_autoindex_devices_1 (name=?)
SEARCH m USING COVERING INDEX sqlite_autoindex_metrics_1 (code=?)
SEARCH r USING INDEX sqlite_autoindex_readings_1 (device_id=? AND metric_id=?)
```
