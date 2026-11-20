# Контракты v1: владельцы и потребители

У каждого стыка один поставщик. Поставщик меняет свою схему и примеры, потребитель проверяет их в своих тестах.
Модели Pydantic в этом пакете — источник правды; JSON Schema и OpenAPI генерируются из них.

| Стык | Где | Поставщик | Потребитель | Статус |
|---|---|---|---|---|
| Ошибка HTTP `{code, message, retryable, traceId}` | `common/errors.py` | № 1 | все | готово |
| Публичный API `/v1` (сессии, классы, варианты, загрузки, привязки, результаты) | `cabinet/`, `examples/cabinet/` | № 1 | фронтенд | OpenAPI стенда: `GET /openapi.json`; вход `POST /v1/sessions` выдаёт cookie; `GET` и `DELETE /v1/sessions/current` читают и отзывают сессию, остальное планируется |
| События `checks.v1`: конверт, `assignment.published`, `attempt.submitted` | `events/checks.py`, `schemas/checks.json`, `examples/events/` | № 1 | № 2 | готово |
| Манифест мини-набора: `caseId`, `split`, `assignmentRef`, `submissionRef`, `expectedReference`, `expectedSteps`, `expectedVerdict`, `expectedIssues`; ZIP с работами в `bundles` | `dataset/manifest.py`, `schemas/dataset.json`, `examples/dataset/` | № 1 | № 2 (шаги, вердикты, замечания, ZIP для E2E), № 3 (`expectedReference`) | готово; генератор `tools/dataset` (A6.2), выпуск версии — A6.3 |
| Внутренние результаты `/internal/v1/checks`, сводка, правки, публикация | `grading/` | № 2 | № 1 | планируется |
| Схема `taskResult` | `grading/taskresult.py` | № 2 | № 1, № 3 | черновик от № 3, ждёт подтверждения № 2 |
| Промпты проверки: файл `<promptVersion>.md` | `grading/prompts/` | № 2 | № 3 (шлюз читает по `promptVersion`) | `review.v1.md` — черновик от № 3, ждёт № 2 |
| API эталонов `/internal/v1/references` | `references/reference.py`, `openapi/references.json`, `examples/references/` | № 3 | № 2 | готово |
| Шлюз модели `/internal/v1/model/review` (конверт) | `references/modelreview.py`, `openapi/gateway.json`, `examples/gateway/` | № 3 | № 2 | готово |
| Управляемые отказы шлюза: 429 и успех, 429 до конца, таймаут, не JSON | `examples/gateway/failures/` (запрос, ответы, число вызовов) | № 3 | № 2 | готово; включаются только `GATEWAY_TRANSPORT=fixture` и `GATEWAY_FAILURE_SCENARIOS=1` |
| Метаданные версии солвера в S3 `references/solvers/<solverVersion>/` | `references/solverartifact.py`, `examples/solvers/` | № 3 (пишет один раз при публикации) | № 3 (читает); № 2 видит только `solverVersion` | зарезервировано, солверов пока нет |

**`taskResult` существует в одном месте.** Конверт ответа шлюза (№ 3) ссылается на `grading/taskresult.py` (№ 2)
и не заводит свою копию полей `answer`, `reasoning`, `method`, `issues`.

**Манифест набора — ключ ответов.** Одна запись — одно задание одной синтетической работы. Манифест не попадает
в DOCX ученика и в ZIP, а задания `closed` не попадают в промпты и фикстуры агентов. Условие (`assignmentRef.statement`)
и эталон (`expectedReference`) — разные поля. Разбиение на `demo`, `e2e` и `closed` идёт по `sourceTaskId`, а не по ученикам.

**`solverVersion` называет одну неизменяемую сборку**: `<familyId>.v<N>`, например `vectorLength.v2`. Ключа `latest` нет.
Исправленный солвер получает новый номер, а старая проверка по своему `solverVersion` находит тот же код и тесты.
`solverVersion=null` допустим только у `source=fixture`.

## Совместимость

- Новые необязательные поля допустимы в v1. Старый потребитель обязан их читать.
- Изменение смысла поля или обязательного состава — новая версия в каталоге `v2/`.
- Изменение обязательного поля согласуется с потребителем до слияния.

## Доступ и ошибки внутренних API

- Каждый вызов `/internal/v1` несёт `Authorization: Bearer <служебный токен>` вызывающего сервиса.
  Cookie учителя внутренние вызовы не авторизует. Проверка: `shared.servicetokens.requireRight`.
- Права операций: `references.request` (POST и GET эталонов), `model.review` (вызов шлюза).
- Любая ошибка — `ApiError {code, message, retryable, traceId}`, коды и статусы в `common/errors.py`.
  Повторяемые коды: `rate_limited`, `unavailable`, `timeout` (с тем же requestId и с учётом Retry-After).
  `conflict` (409), `invalid_input` (422), `personal_data_suspected` (422, только шлюз) и `model_invalid_output` (502)
  повторять бессмысленно.
- Текст ошибки фиксированный: без тела запроса, текста работы и секретов. `traceId` берётся из заголовка `X-Trace-Id`.
