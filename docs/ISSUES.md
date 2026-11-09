# План задач (issues) і гілок

Мета — щоб історія репозиторію показувала **реальний** ланцюг `issue → branch → PR → коміти → review → merge` (B1)
та виконання зобов'язань у строк (B2). Дедлайни поставте самі, виходячи з дат ваших чекпоінтів.

Правила:

* одна задача = один issue = одна гілка `feature/<N>-<коротко>` = один PR (`Closes #N`);
* коміт: `тип(область): що зроблено (#N)`, типи: `feat`, `fix`, `test`, `docs`, `refactor`, `ci`;
* `main` захищена від прямих комітів (Settings → Branches), merge лише через PR із зеленим CI;
* зауваження викладача на чекпоінтах → окремі issue з міткою `feedback` (A3).

| # | Задача (issue) | Гілка | Що в PR | Definition of Done |
|---|---|---|---|---|
| 1 | Каркас репозиторію, CI, шаблони | `feature/1-scaffold` | `pyproject.toml`, `.github/*`, `.gitignore`, `.pylintrc` | CI запускається на 3 ОС |
| 2 | Моделі даних і валідація | `feature/2-models-validation` | `models.py`, `validation.py` + тести | межові значення покриті тестами |
| 3 | Муляж датчика | `feature/3-simulated-sensor` | `sensors/*` + тести | відтворювані дані за `seed`, збої працюють |
| 4 | Схема SQLite і сховище | `feature/4-storage` | `storage.py` + тести | FK, `UNIQUE`, індекси, транзакції, ін'єкції |
| 5 | Розрахунок AQI | `feature/5-aqi` | `aqi.py` + тести | усі брейкпоінти EPA перевірено |
| 6 | Сповіщення з гістерезисом | `feature/6-alerts` | `alerts.py` + тести | немає «мигання» біля порога |
| 7 | Конвеєр обробки | `feature/7-pipeline` | `pipeline.py` + тести | лічильники, переповнення черги |
| 8 | Store-and-forward, backoff | `feature/8-uplink` | `uplink.py` + тести | обрив хмари без втрат і дублікатів |
| 9 | EdgeNode (потоки, зупинка) | `feature/9-runtime` | `runtime.py` + тести | коректна зупинка, відновлення після обриву |
| 10 | REST API та дашборд | `feature/10-api` | `api.py` + тести | 400/404, порожня БД, ін'єкції |
| 11 | CLI | `feature/11-cli` | `cli.py`, `__main__.py` + тести | `run` і `report` працюють |
| 12 | Приймальні тести TC-01..TC-11 | `feature/12-acceptance` | `tests/test_acceptance.py`, `docs/TEST_REPORT.md` | 11/11 пройдено |
| 13 | Звіти якості (покриття, pylint) | `feature/13-quality` | `scripts/*`, CI-гейт 80% | покриття ≥ 80%, pylint без зауважень |
| 14 | README та матеріали до захисту | `feature/14-docs` | `README.md`, `docs/*` | запуск з нуля за README без втручання |

## Таблиця виконання (для B2)

Скопіюйте в GitHub Projects (Table) або залишіть тут, але заповнюйте **під час роботи**.

| Issue | Дедлайн | Дата закриття | Статус | Якщо запізнення: коли й де повідомлено про ризик |
|---|---|---|---|---|
| #1 | | | | |
| #2 | | | | |
| … | | | | |

## Приклад повного ланцюга (для B1)

```
Issue #8  «Store-and-forward, backoff»          (шаблон «Задача», дедлайн, DoD)
  └─ гілка feature/8-uplink
       ├─ feat(uplink): sync_once та FileUplink (#8)
       ├─ test(uplink): обрив хмари без втрат (#8)
       └─ docs(logbook): рішення про catch-up (#8)
  └─ PR «Closes #8» → CI зелений
       └─ review (коментар → виправлення → повторна перевірка)
  └─ merge у main → issue автоматично закрито
```
