# Контракты v1: владельцы и потребители

У каждого стыка один поставщик. Поставщик меняет свою схему и примеры, потребитель проверяет их в своих тестах.
Модели Pydantic в этом пакете — источник правды; JSON Schema и OpenAPI генерируются из них.

| Стык | Где | Поставщик | Потребитель | Статус |
|---|---|---|---|---|
| Ошибка HTTP `{code, message, retryable, traceId}` | `common/errors.py` | № 1 | все | готово |
| Публичный API `/v1` (сессии, классы, варианты, загрузки, привязки, результаты) | `cabinet/` | № 1 | фронтенд | планируется |
| События `checks.v1`: конверт, `assignment.published`, `attempt.submitted` | `events/` | № 1 | № 2 | планируется |
| Манифест мини-набора | `dataset/` | № 1 | № 2, № 3 | планируется |
| Внутренние результаты `/internal/v1/checks`, сводка, правки, публикация | `grading/` | № 2 | № 1 | планируется |
| Схема `taskResult` | `grading/taskresult.py` | № 2 | № 1, № 3 | черновик от № 3, ждёт подтверждения № 2 |
| API эталонов `/internal/v1/references` | `references/reference.py`, `openapi/references.json`, `examples/references/` | № 3 | № 2 | готово |
| Шлюз модели `/internal/v1/model/review` (конверт) | `references/modelreview.py`, `openapi/gateway.json`, `examples/gateway/` | № 3 | № 2 | готово |

**`taskResult` существует в одном месте.** Конверт ответа шлюза (№ 3) ссылается на `grading/taskresult.py` (№ 2)
и не заводит свою копию полей `answer`, `reasoning`, `method`, `issues`.

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
  `conflict` (409), `invalid_input` (422) и `model_invalid_output` (502) повторять бессмысленно.
- Текст ошибки фиксированный: без тела запроса, текста работы и секретов. `traceId` берётся из заголовка `X-Trace-Id`.
