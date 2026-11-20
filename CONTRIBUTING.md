# Правила репозитория

## Имена

| Что | Как писать | Пример |
|---|---|---|
| Собственные функции, методы, параметры, переменные | lowerCamelCase | `getCheck`, `resultRev` |
| Поля моделей Pydantic и ключи JSON | lowerCamelCase, одинаково в HTTP, Redis и манифесте датасета | `checkId`, `traceId` |
| Классы, модели, Enum, Protocol | PascalCase | `ApiError`, `ReferenceProvider` |
| Модули и пакеты | одно слово в нижнем регистре | `errors.py`, `docxreader.py` |
| Тестовые файлы и функции | `test` + camelCase | `testErrors.py`, `testApiErrorRoundTrip` |
| Значения enum и имена событий | дословно из `contracts/v1` | `needs_review`, `attempt.submitted` |

Стандартные имена Python, библиотек и протоколов не переименовываем: `__init__`, `model_config`,
`model_dump_json`, заголовок `Retry-After`, параметры FastAPI и SQLAlchemy.

**Имя поля и значение — разные вещи.** `needsReview` — это поле, `needs_review` — значение статуса.
Автоматическая замена регистра не должна трогать строковые значения.

Никаких отдельных DTO с `trace_id` для HTTP: модель из `contracts/v1` и есть формат провода.

### Пример: модель ошибки

```python
from contracts.v1.common import ApiError

error = ApiError(code="conflict", message="Stale revision", retryable=False, traceId="t1")
wire = error.model_dump_json()
# {"code":"conflict","message":"Stale revision","retryable":false,"traceId":"t1"}
assert ApiError.model_validate_json(wire) == error
```

## Стенд

`deploy/compose.yaml` поднимает PostgreSQL, Redis Streams и S3-совместимый SeaweedFS. Данные лежат в именованных
томах и переживают перезапуск.

| Что | Адрес по умолчанию | Переменная порта |
|---|---|---|
| PostgreSQL | `127.0.0.1:15432`, база `gorky`, администратор `gorky` / `gorky` | `POSTGRES_PORT` |
| Redis | `redis://127.0.0.1:16379/0` | `REDIS_PORT` |
| S3 (SeaweedFS) | `http://127.0.0.1:18333`, ключ администратора `gorky-dev` / `gorky-dev-secret` | `S3_PORT` |

Адреса и тестовые учётные данные для клиентов перечислены в `.env.example`. Внутри сети Compose сервисы доступны
по именам `postgres:5432`, `redis:6379`, `seaweedfs:8333`. Ключи S3 задаются в `deploy/seaweedfs/s3.json`.

- Запуск с ожиданием готовности (команда ждёт, пока каждый сервис начнёт принимать запросы): `docker compose -f deploy/compose.yaml up -d --wait`
- Состояние: `docker compose -f deploy/compose.yaml ps`, у всех сервисов должно быть `healthy`.
- Проверка записи и чтения во всех трёх хранилищах: `uv run pytest tests/infra`
- Остановка без потери данных: `docker compose -f deploy/compose.yaml down`. С флагом `-v` тома удаляются.

### Роли, схемы и бакеты сервисов

Учётки администратора нужны только стенду и тестам. Каждый сервис ходит в хранилища под своей ролью и своим
ключом и видит только своё. Миграция сервиса не может задеть чужие таблицы: у его роли там нет прав.

| Сервис | Роль PostgreSQL и схема | Ключ S3 | Бакеты |
|---|---|---|---|
| Кабинет (№ 1) | `gorky_cabinet`, схема `cabinet` | `gorky-cabinet` | `sources`: чтение и запись; `results`: чтение |
| Проверка (№ 2) | `gorky_grading`, схема `grading` | `gorky-grading` | `sources`: чтение; `results`: чтение и запись |
| Эталоны и шлюз (№ 3) | `gorky_references`, схема `refs` | `gorky-references` | `artifacts`: чтение и запись |

- Роли и схемы создаёт `deploy/postgres/init/10-roles.sql`. PostgreSQL выполняет его сам при первом запуске
  пустого тома. Стенду, поднятому до A1.2, его нужно выполнить один раз (скрипт можно запускать повторно,
  таблицы, созданные раньше администратором, он передаёт роли сервиса):
  `docker compose -f deploy/compose.yaml exec postgres psql -v ON_ERROR_STOP=1 -U gorky -d gorky -f /docker-entrypoint-initdb.d/10-roles.sql`
- Схему создаёт только этот скрипт, роль сервиса создавать схемы не может. Alembic каждого сервиса работает
  под ролью сервиса и пишет `alembic_version` в свою схему. Миграции нового сервиса лежат внутри его пакета
  (`cabinet/migrations`), чтобы mypy не видел два модуля `migrations.env`.
- Права на бакеты задаёт `deploy/seaweedfs/s3.json`, имена бакетов лежат в `shared/buckets.py`. Бакеты создаёт
  администратор: сейчас это делает `uv run pytest tests/infra`.
- Пароли и ключи сервисов годятся только для стенда. Служебные токены `/internal/v1` (`*_SERVICE_TOKENS`) и
  cookie учителя на `/v1` (A2.2) — разные учётные данные с разным назначением: токен не принимается как сессия
  учителя, и наоборот.

Если в Windows включён системный прокси (например, VPN-клиент), HTTP-клиенты Python отправляют через него и
запросы на `127.0.0.1`. Клиенты стенда должны отключать прокси явно, как это сделано в `tests/infra/testStand.py`.

## Мини-набор

Генератор `tools/dataset` по seed пишет варианты, работы учеников, ZIP с работами каждого варианта и
`manifest.json` (ключ ответов, схема — `contracts/v1/dataset`):

```
uv run python -m dataset.generate --seed 20261120 --out <пустой каталог>
```

- Один seed даёт те же байты на любом запуске и ОС. Поэтому в DOCX и ZIP нет текущего времени, ОС и сжатия
  (`dataset/archive.py`), а значения берутся из `SeededDraws`, а не из порядка вызовов `random`.
- Элемент ZIP — побайтно тот же файл, что лежит отдельно в `submissions/`. Для проверки дубликата берём эти
  байты, а не генерируем похожий Word заново.
- Манифест не кладём в DOCX и ZIP. Задания `closed` не попадают в промпты и фикстуры.
- Имена учеников синтетические. Наборы «Математика 1» и «Математика 2» в git не кладём.

## Тесты

- Все тесты собираются одной командой из корня: `uv run pytest`. Проверить состав: `uv run pytest --collect-only -q`.
- pytest собирает файлы `test*.py` и функции `test*`. Вспомогательные функции и фикстуры не начинаем с `test`,
  иначе pytest примет их за тест: `buildEvent`, `makeClient`, а не `testClient`.
- Перед полным прогоном поднять стенд: `docker compose -f deploy/compose.yaml up -d --wait`.
  В начале прогона тесты под администратором (`DATABASE_URL`) очищают схемы сервисов, выполняют
  `10-roles.sql` и миграции каждого сервиса под его ролью (`CABINET_DATABASE_URL`, `REFERENCES_DATABASE_URL`).
  По умолчанию все адреса ведут на `127.0.0.1:15432` из Compose. Если база недоступна, тесты падают с подсказкой.
  Пропуск таких тестов не допускается.
- Строгая типизация: `uv run mypy` (настройки и список целей в корневом `pyproject.toml`).

### Быстрые и сквозные проверки

| Когда | Команда |
|---|---|
| После каждого шага (каноническая) | `docker compose -f deploy/compose.yaml up -d --wait`, затем `uv run pytest` |
| Отладка одного модуля | `uv run pytest tests/references` или `uv run pytest -k testFixture` |
| Быстрый прогон без сквозного сценария | `uv run pytest -m "not e2e"` |
| Только сквозной сценарий | `uv run pytest -m e2e` |

Каноническая команда запускает всё, включая E2E. Быстрый выбор — только для отладки и не заменяет полный прогон
перед коммитом.

Маркер `e2e` зарегистрирован в `pyproject.toml`; из-за `--strict-markers` опечатка в имени маркера роняет сбор.
Пока сценария нет, `uv run pytest -m e2e` выбирает ноль тестов — пустой зелёный тест-заглушку не добавляем.

Будущий E2E (шаги E1–E5):

- лежит в `tests/e2e/`, каждый тест помечен `@pytest.mark.e2e` (или `pytestmark = pytest.mark.e2e` на модуль);
- работает с поднятым стендом Compose через настоящий HTTP, Redis, PostgreSQL и S3;
- при недоступном стенде падает с подсказкой команды запуска, как тесты реестра эталонов;
- не исключается через `addopts`, `-m` по умолчанию, `skip` или `skipif`.

## Коммиты

Сообщение целиком на английском: `type(scope): description`.

- `type` — `core` (новое поведение), `fix`, `test`, `docs`.
- `scope` — фактический файл, библиотека или каталог: `contracts`, `references`, `pyproject.toml`.
- `description` — с глагола, коротко: `core(references): add the reference registry`.

Один шаг чек-листа — один коммит. Несвязанные шаги разных разработчиков в один коммит не объединяем.
