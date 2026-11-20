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

`deploy/compose.yaml` поднимает API кабинета, PostgreSQL, Redis Streams и S3-совместимый SeaweedFS. Данные лежат в именованных
томах и переживают перезапуск.

| Что | Адрес по умолчанию | Переменная порта |
|---|---|---|
| Кабинет | `http://127.0.0.1:18080` | `CABINET_PORT` |
| PostgreSQL | `127.0.0.1:15432`, база `gorky`, администратор `gorky` / `gorky` | `POSTGRES_PORT` |
| Redis | `redis://127.0.0.1:16379/0` | `REDIS_PORT` |
| S3 (SeaweedFS) | `http://127.0.0.1:18333`, ключ администратора `gorky-dev` / `gorky-dev-secret` | `S3_PORT` |

Адреса и тестовые учётные данные для клиентов перечислены в `.env.example`. Свои значения и секреты кладём в
`.env` в корне (его нет в git и в образах): сервисы, запущенные из корня через `uvicorn ... --factory` или
`python -m cabinet.*`, читают его сами, а переменная, уже заданная в сессии, важнее файла. pytest `.env` не читает. Внутри сети Compose сервисы доступны
по именам `postgres:5432`, `redis:6379`, `seaweedfs:8333`. Ключи S3 задаются в `deploy/seaweedfs/s3.json`.

- Запуск с ожиданием готовности (команда ждёт, пока каждый сервис начнёт принимать запросы): `docker compose -f deploy/compose.yaml up -d --wait`
- Состояние: `docker compose -f deploy/compose.yaml ps`, PostgreSQL, Redis, SeaweedFS и кабинет должны быть `healthy`, `outbox-publisher` — `running`.
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

### Публичное API кабинета

Из корня проекта запустите `docker compose -f deploy/compose.yaml up -d --build --wait`.
Кабинет применяет свои миграции при старте. Проверка `GET /ready` возвращает 200,
когда роль кабинета может прочитать таблицы входа, и 503 при ошибке БД.

Фронтенду доступны [Swagger UI](http://127.0.0.1:18080/docs) и [OpenAPI](http://127.0.0.1:18080/openapi.json).
Публичные операции начинаются с `/v1`. В сети Compose адрес кабинета для других сервисов — `http://cabinet:8000`.

Стенд по умолчанию — хранилища, кабинет и outbox publisher: его используют тесты, которые очищают схемы сервисов.
Профиль `full` добавляет `grading:8000` (API результатов), `grading-worker`, `references:8000` и `gateway:8000`,
и загруженный учителем DOCX доходит до результата и Markdown:
`docker compose -f deploy/compose.yaml --profile full up -d --build --wait`. На нём работают фронтенд и ручные
проверки. Под pytest полный профиль не держим: воркер стенда разбирал бы задания тестов `tests/grading`.
Шлюз отвечает записанными fixture, пока в сессии не заданы `GATEWAY_TRANSPORT=gigachat` и `GIGACHAT_AUTH_KEY`.

Пример ответа `401` на `POST /v1/sessions`:

```json
{"code":"unauthorized","message":"Wrong email or password","retryable":false,"traceId":"t-login-1"}
```

Идентификатор также приходит в заголовке `X-Trace-Id`.

Для `GET /v1/checks/{checkId}` задайте адрес API проверки в `CABINET_GRADING_BASE_URL` и токен кабинета
в `CABINET_GRADING_SERVICE_TOKEN`. Зарегистрируйте этот токен в `grading` с правом `checks.read`.
Compose по умолчанию использует `http://grading:8000`. Тесты кабинета проверяют стык на HTTP-заглушке.
Код развёрнутого `grading` должен реализовать [внутренний контракт чтения](contracts/contracts/v1/openapi/grading.json).

При сбое загрузки кабинет очищает DOCX в префиксе `sources/uploads/<uploadId>/` после проверки PostgreSQL.
Загрузка и очистка используют одну блокировку для данного `uploadId`. Она защищает исходники при обрыве
соединения во время COMMIT. Если процесс прервался или хранилище было недоступно, повторите очистку конкретной
загрузки командой `uv run python -m cabinet.cleanupuploads <uploadId>`. Ссылки из загрузок и попыток сохраняются.

### Откат миграции кабинета 0016

Перед обновлением с `0015` остановите процессы, которые пишут в PostgreSQL, S3 или Redis, включая кабинет
и публикацию outbox. Сохраните дамп PostgreSQL в формате `pg_dump -Fc` и снимки S3 и Redis за этот период.
Проверьте восстановление дампа на отдельной базе.

В `0016` удалённая сдача остаётся в истории, а повторная загрузка того же файла для того же ученика и варианта
создаёт новую попытку. Если в базе есть такая пара, `alembic downgrade 0015` завершается ошибкой уникальности.
PostgreSQL откатывает транзакцию миграции, поэтому версия `0016`, частичный уникальный индекс и обе попытки сохраняются.

Для отката остановите запись и потребителей, затем восстановите резервную копию `0015` в отдельную пустую базу
через `pg_restore --exit-on-error` со служебными ролями исходной базы. Подключите прежнюю версию кабинета к этой базе
и согласованным снимкам хранилищ. Проверьте `cabinet.alembic_version`, доступность исходных DOCX и привязок,
затем переключите трафик. Сохраните текущую базу для отдельной сверки записей, созданных после резервной копии.

### Публикация событий кабинета

Compose запускает отдельный процесс `outbox-publisher`. Для локального запуска процесса вне Docker
используйте `uv run python -m cabinet.outboxpublisher` после миграций кабинета. Адреса берутся из
`CABINET_DATABASE_URL` и `REDIS_URL`.

Процесс публикует конверт в поле `data` потока `checks.v1` и отмечает строку в PostgreSQL после подтверждения Redis.
При сбое он повторяет передачу с прежним `eventId`. Потребитель использует этот идентификатор для дедупликации.
По `traceId` и `eventId` в логах `outbox-publisher` можно найти подтверждение публикации или повтор.
Синтетические сообщения и DOCX для сервиса проверки хранятся в [примере передачи работ](contracts/contracts/v1/examples/handoff/README.md).

### Регистрация и вход учителя

`POST /v1/teachers/register` принимает `email`, `displayName` и `password` длиной от 8 символов.
При успехе кабинет создаёт аккаунт и возвращает `201` с `teacherId` и `displayName`. Затем запросите сессию
через `POST /v1/sessions`, передав `email` и `password`. Адрес сравнивается без учёта регистра, а повторная
регистрация существующего адреса возвращает `409`.

Кабинет запускается командой `uv run uvicorn cabinet.main:createApp --factory`. Учителя для стенда создаёт
`uv run python -m cabinet.seedteacher`. Адрес и пароль берутся из `CABINET_DEMO_TEACHER_EMAIL` и
`CABINET_DEMO_TEACHER_PASSWORD` (`.env.example`). Стандартный адрес демо-учителя — `teacher.demo@example.org`.
Пароли хранятся только как хеш Argon2id. Неверный пароль и неизвестный адрес дают один и тот же `401`.
После `CABINET_LOGIN_MAX_FAILURES` неудач вход по этому адресу блокируется на `CABINET_LOGIN_WINDOW_S` секунд.

Cookie `__Host-cabinet` действует 8 часов. Используйте HTTPS (для локальной разработки — localhost).
Кабинет и браузерный фронтенд работают через один origin. После входа `GET /v1/sessions/current`
возвращает учителя, а `DELETE /v1/sessions/current` отзывает сессию и отвечает 204.

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
- Эталоны открытых выборок (`demo`, `e2e`) сверены вручную и записаны с исходными числами и расчётом в
  `services/references/references/fixtures/openSetKeys.json` (версия `mini.v1`, seed 20261120).
  `tests/references/testOpenSetKeys.py` берёт условие из Word-файла, считает значение заново и сравнивает
  манифест с ключами, называя `caseId` расхождения. После смены seed, версии или заданий открытых выборок
  ключи пересчитывают вручную, а не копируют из манифеста.
- Фикстуры № 3 для открытых выборок (`services/references/references/fixtures/references.json` и `reviews.json`)
  совпадают с этими ключами и с ответами манифеста. Обмены для № 2 лежат в `contracts/v1/examples/references/exchanges/`
  и `contracts/v1/examples/gateway/exchanges/`: поставщик прогоняет их через свои маршруты
  (`tests/references/testServiceFixtures.py`), клиент № 2 читает те же файлы (`tests/grading/testServiceExamples.py`).
- Приёмка стыка воркера № 2 с сервисами № 3 по HTTP: `uv run pytest tests/grading/testServiceHandoff.py`. Реестр и
  шлюз поднимаются в uvicorn с фикстурами, воркер проверяет вариант demo1 мини-набора (готовый результат), ожидание
  pending → ready и ручную проверку с reasonCode. Платный провайдер не вызывается.
- Живая проверка GigaChat — один платный вызов, по умолчанию пропускается. `GIGACHAT_AUTH_KEY=<ключ>` кладём
  в свой `.env` в корне или в переменные сессии, в git — никогда. Включатель `GIGACHAT_LIVE_SMOKE=1` задаём только
  в сессии, чтобы полный прогон не платил: `uv run pytest tests/gateway/testGigaChatLive.py -s`. Тест проверяет вход, TLS, промпт `review.v2` и что ответ —
  валидный `taskResult`; вердикт только печатается.

### Мини-набор mini.v1 для разработки и сверки

В [открытом комплекте](tools/dataset/releases/mini.v1/) с `seed=20261120` есть DOCX, ZIP и
[манифест](tools/dataset/releases/mini.v1/manifest.json) с двумя выборками.

- `demo` содержит верную и ошибочную работы, пропущенное задание и ответ без шагов. Пропуск представлен
  переписанным условием без решения и размечен как `skipped`.
- `e2e` использует `assignments/e2e1.docx` и `bundles/e2e1.zip`. Оба ученика получили верный ответ. У второго
  есть одно замечание категории `sign` к `s2`, которое учитель снимает в сценарии приёмки.

Воспроизвести комплект в пустом каталоге можно командой `uv run python -m dataset.generate --open-only --seed 20261120 --out /tmp/gorky-mini-v1`.
Тест сверяет все файлы с опубликованной копией побайтно и проверяет ссылки в манифесте. Для закрытой приёмки
команда оценки использует тот же открытый seed и задаёт свой `--closed-seed`. Генератор исключает пересечения
по `sourceTaskId`. Закрытый seed и полный результат генерации хранятся у команды оценки.

| Что сверяем перед приёмкой | Статус |
|---|---|
| Шаги, замечания и трактовка пропуска с разработчиком № 2 | Ожидает сверки |
| Математические ключи с разработчиком № 3 | Сверены для `demo` и `e2e`. Расчёты зафиксированы в `openSetKeys.json`. |

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

При пуше и pull request в `main` workflow [Backend checks](.github/workflows/backend.yml) поднимает отдельный
Compose-проект с динамическими портами и выполняет три обязательные стадии: строгий mypy, все тесты кроме
сквозного (`-m "not e2e"`), затем сквозной сценарий (`-m e2e`) на своём изолированном полном стенде.
Отдельный шаг проверяет по JUnit-отчёту, что сценарий выполнен и не пропущен. Отчёты `reports/*.xml`,
цепочка идентификаторов `reports/e2e-journey.json` и при сбое логи сервисов `reports/e2e-stand.log`
сохраняются артефактом `backend-test-reports`. По завершении удаляются контейнеры и тома только этого запуска.

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

### Сквозной сценарий E2E

`tests/e2e/testTeacherJourney.py` проходит путь учителя по публичному `/v1`: регистрация и вход, класс и два
ученика, вариант и ZIP открытого набора `mini.v1` (`e2e1`), привязка работ, повторная загрузка того же DOCX,
результаты проверки, отчёт по классу, правка учителя и её повтор, публикация и скачивание Markdown. Внутри
работают настоящие кабинет, outbox, Redis, воркер `grading`, реестр эталонов, шлюз (fixture, без платной
модели), PostgreSQL и S3.

Фикстура `e2eStand` (`tests/e2e/conftest.py`) сама поднимает отдельный Compose-проект `gorky-e2e-<id>` с
профилем `full`, своими томами и свободными портами, ждёт готовности и удаляет его после прогона. Поэтому
сценарий не видит стенд разработчика и старые результаты, а два запуска не делят данные. Нужен Docker.

| Задача | Команда |
|---|---|
| Воспроизвести проверку целиком (как CI) | `docker compose -f deploy/compose.yaml up -d --build --wait`, затем `uv run mypy` и `uv run pytest` |
| Только сценарий | `uv run pytest -m e2e -rs` |
| Не пересобирать образы `gorky-*:dev` | `E2E_BUILD=0 uv run pytest -m e2e` |
| Оставить стенд сценария для разбора | `E2E_KEEP=1 uv run pytest -m e2e -s` |
| Сценарий на уже поднятом полном стенде | `E2E_CABINET_URL=http://127.0.0.1:18080 uv run pytest -m e2e` |

В итоге прогона не должно быть `skipped` для этого сценария. Где искать последний этап сбоя:

1. Сообщение теста начинается с `E2E step E…` и содержит последний HTTP-статус, `traceId`, `checkId`,
   наблюдаемое состояние проверки и имя Compose-проекта. Токенов, cookie и текста решений в нём нет.
2. Каждый запрос сценария идёт со своим `X-Trace-Id` (`e2e-…`). Кабинет передаёт его в `grading`, событие
   `checks.v1` несёт его в `traceId`: `docker compose -p <project> --profile full logs cabinet grading-worker | grep <traceId>`.
3. Этап проверки виден по `checkId`: `GET /v1/checks/<checkId>` даёт `state` и `reason`, а в базе —
   `grading.check_jobs.state` и `completed_stage`; ошибки воркера пишутся в лог `grading-worker` с `traceId`.
4. Ответ шлюза — лог `gateway` (события `review.*` с `requestId` и `traceId`), эталон — лог `references`.

Протокол совместной приёмки: [docs/acceptance/backend.md](docs/acceptance/backend.md).

## Коммиты

Сообщение целиком на английском: `type(scope): description`.

- `type` — `core` (новое поведение), `fix`, `test`, `docs`.
- `scope` — фактический файл, библиотека или каталог: `contracts`, `references`, `pyproject.toml`.
- `description` — с глагола, коротко: `core(references): add the reference registry`.

Один шаг чек-листа — один коммит. Несвязанные шаги разных разработчиков в один коммит не объединяем.

## Порядок закрытия шага

Каждый шаг чек-листа закрывается одинаково, в этом порядке:

1. **Изменение.** Только файлы этого шага. Чужой незаконченный шаг в рабочей копии не трогаем.
2. **Стенд.** `docker compose -f deploy/compose.yaml up -d --wait`.
3. **Весь pytest.** `uv run pytest` из корня, без `-m` и без пути к модулю: так запускается всё, включая E2E.
   Если менялся Python, ещё `uv run mypy`.
4. **Сбой — исправить и повторить.** Пока чините, можно гонять один упавший тест
   (`uv run pytest tests/dataset -k testZip`), но после исправления снова весь `uv run pytest`.
   Зелёный прогон одного модуля не заменяет общий. Пропускать или помечать `skip` упавший тест нельзя.
5. **Отдельный коммит.** `git status`, затем `git add` с файлами этого шага по именам (не `git add -A`),
   затем `git commit`. Сообщение по правилам выше, целиком на английском. В одном коммите один шаг одного
   разработчика.
6. **Отметка.** В чек-листе `- [ ]` → `- [x]` у этого шага, статус в таблице работ.

Пример сообщения для этого шага:

```
docs(workflow): record the per-step test and commit routine
```

Так нельзя: `docs: update` (нет каталога в скобках), `core(dataset): генератор` (не английский),
`fix(dataset, auth): ...` (два несвязанных шага в одном коммите).
