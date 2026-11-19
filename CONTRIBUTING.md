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

## Тесты

- Все тесты собираются одной командой из корня: `uv run pytest`. Проверить состав: `uv run pytest --collect-only -q`.
- pytest собирает файлы `test*.py` и функции `test*`. Вспомогательные функции и фикстуры не начинаем с `test`,
  иначе pytest примет их за тест: `buildEvent`, `makeClient`, а не `testClient`.
- Перед полным прогоном поднять стенд: `docker compose -f deploy/compose.yaml up -d --wait`.
  Тесты реестра эталонов берут адрес базы из `REFERENCES_DATABASE_URL` (по умолчанию `127.0.0.1:15432` из Compose, порт меняется через `POSTGRES_PORT`)
  и падают с подсказкой, если база недоступна. Пропуск таких тестов не допускается.
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
