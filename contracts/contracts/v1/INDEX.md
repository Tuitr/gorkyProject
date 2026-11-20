# Контракты v1: владельцы и потребители

У каждого стыка один поставщик. Поставщик меняет свою схему и примеры, потребитель проверяет их в своих тестах.
Модели Pydantic в этом пакете — источник правды; JSON Schema и OpenAPI генерируются из них.

| Стык | Где | Поставщик | Потребитель | Статус |
|---|---|---|---|---|
| Ошибка HTTP `{code, message, retryable, traceId}` | `common/errors.py` | № 1 | все | готово |
| Регистрация учителя `POST /v1/teachers/register` | `cabinet/registration.py`, [пример запроса](examples/cabinet/teacherRegistration.json) | № 1 | фронтенд | `201` с `TeacherSession`, `409` если адрес уже занят, `422` если поля невалидны. Вход `POST /v1/sessions` принимает `email` и `password` |
| Публичный API `/v1` (сессии, классы, варианты, загрузки, привязки, результаты) | `cabinet/`, `examples/cabinet/` | № 1 | фронтенд | OpenAPI стенда: `GET /openapi.json`; вход `POST /v1/sessions` выдаёт cookie; `GET` и `DELETE /v1/sessions/current` читают и отзывают сессию; `GET` и `POST` на `/v1/classes` и `/v1/classes/{classId}/students` доступны владельцу класса; `POST /v1/classes/{classId}/assignments` сохраняет версии варианта; `POST /v1/assignments/{assignmentId}/uploads` принимает DOCX или ZIP с работами; `GET /v1/uploads/{uploadId}` возвращает состояния файлов; `POST /v1/uploads/{uploadId}/bindings` подтверждает ученика |
| События `checks.v1`: конверт, `assignment.published`, `attempt.submitted` | `events/checks.py`, `schemas/checks.json`, `examples/events/` | № 1 | № 2 | записываются в PostgreSQL outbox и доставляются в Redis Streams |
| Манифест мини-набора: `caseId`, `split`, `assignmentRef`, `submissionRef`, `expectedReference`, `expectedSteps`, `expectedVerdict`, `expectedIssues`; ZIP с работами в `bundles` | `dataset/manifest.py`, `schemas/dataset.json`, `examples/dataset/` | № 1 | № 2 (шаги, вердикты, замечания, ZIP для E2E), № 3 (`expectedReference`) | эталоны [открытого mini.v1](../../../tools/dataset/releases/mini.v1/) сверены; разметка ожидает сверки № 2 |
| Чтение проверки `/internal/v1/checks/{checkId}` | `grading/checks.py`, [OpenAPI](openapi/grading.json), [примеры](examples/grading/) | № 2 | № 1 | стык B5.1 и A7.1, сверен сквозным тестом `tests/e2e`. Кабинет читает его через `GET /v1/checks/{checkId}` |
| Схема `taskResult` | `grading/taskresult.py` | № 2 | № 1, № 3 | готово: № 2 принял схему в `grading/review.py` (B4.2) |
| Промпты проверки: файл `<promptVersion>.md` | `grading/prompts/` | № 2 | № 3 (шлюз читает по `promptVersion`) | готово: действует `review.v2.md` (№ 2, `grading/review.py`); `review.v1` снят |
| API эталонов `/internal/v1/references` | `references/reference.py`, `openapi/references.json`, `examples/references/`; обмены для № 2 (ready, pending → ready, unsupported, validation_failed) — `examples/references/exchanges/` | № 3 | № 2 | готово |
| Шлюз модели `/internal/v1/model/review` (конверт) | `references/modelreview.py`, `openapi/gateway.json`, `examples/gateway/`; ответы по известным stepId мини-набора — `examples/gateway/exchanges/` | № 3 | № 2 | готово |
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
- Права операций: `checks.read` (чтение проверки), `references.request` (POST и GET эталонов), `model.review` (вызов шлюза).
- Любая ошибка — `ApiError {code, message, retryable, traceId}`, коды и статусы в `common/errors.py`.
  Повторяемые коды: `rate_limited`, `unavailable`, `timeout` (с тем же requestId и с учётом Retry-After).
  `conflict` (409), `invalid_input` (422), `personal_data_suspected` (422, только шлюз) и `model_invalid_output` (502)
  повторять бессмысленно.
- Текст ошибки фиксированный: без тела запроса, текста работы и секретов. `traceId` берётся из заголовка `X-Trace-Id`.

### Удаление класса и ученика

Владелец может удалить класс через `DELETE /v1/classes/{classId}` или ученика через
`DELETE /v1/classes/{classId}/students/{studentId}`. Успешный запрос возвращает `204` без тела.
Чужие, отсутствующие или уже удалённые записи возвращают `404`.

Удалённый класс исчезает из списка. Его варианты, загрузки, проверки и отчёты также становятся недоступны
по прежним адресам. Удалённый ученик исчезает из списка класса и кандидатов для новых привязок. Сданные
ранее работы и связанные с ними исторические результаты сохраняются.

### Список и загрузка вариантов

`GET /v1/classes/{classId}/assignments` возвращает владельцу по одной записи на активный вариант.
Запись содержит `assignmentId`, номер последней ревизии `assignmentRev` и её дату `assignmentDate` (YYYY-MM-DD).
Порядок от новых дат к старым, при совпадении дат по возрастанию `assignmentId`.
Если активных вариантов нет, ответ содержит `[]`. Для чужого, отсутствующего или удалённого класса возвращается `404`.

`POST /v1/classes/{classId}/assignments` принимает `multipart/form-data`. Поле `file` содержит DOCX.
В поле `requirements` передаётся массив в формате JSON с элементами `{"text":"Решить через теорему Виета","taskNumber":"2"}`.
Пустой массив означает, что дополнительных требований нет. `taskNumber: null` применяет требование ко всему варианту.

Поле `assignmentDate` необязательно. Формат даты — `YYYY-MM-DD`, по умолчанию используется дата загрузки в UTC.
Чтобы сохранить новую ревизию, повторите запрос с тем же `assignmentId` и передайте DOCX и полный список требований.
Ответ `201` содержит `assignmentId` и `assignmentRev`. Каждая ревизия сохраняет свою дату, требования и DOCX.

### Загрузка работы ученика

`POST /v1/assignments/{assignmentId}/uploads` принимает `multipart/form-data`.
Поле `file` содержит один DOCX до 20 МиБ или архив `.zip` до 50 МиБ. Поле `assignmentRev` задаёт ревизию варианта.
В ZIP допустимы до 100 DOCX, до 200 записей с учётом каталогов и до 100 МиБ распакованных файлов.
Кабинет проверяет весь архив до записи и создаёт для каждого документа отдельный `fileId`.
Ответ `202` содержит `uploadId`. Учитель может загрузить работу только к ревизии варианта своего класса.

`GET /v1/uploads/{uploadId}` возвращает `uploadId` и список `files` со статусом каждого документа.
Запись содержит `fileId`, `originalName`, `status`, список `candidates` из учеников этого класса и `reason`, если есть причина отказа или уточнения.
Кабинет сверяет имя файла и подпись в начале DOCX со списком класса. Поддерживаются подписи
`Ученик:`, `Ученица:`, `ФИО:` и `Выполнил(а):`. При единственном полном совпадении без противоречий
кабинет привязывает файл к ученику и присваивает статус `bound`. Совпадение по части имени, конфликт и отсутствие совпадений
дают `needs_confirmation`. Для отказа предусмотрен `rejected`. Ранее загруженный файл со статусом
`pending_binding` проходит ту же сверку при чтении прогресса.
У файла со статусом `bound` есть `studentId`, `attemptId` и `checkId`.
Чужой и несуществующий `uploadId` возвращают `404`.

`POST /v1/uploads/{uploadId}/bindings` подтверждает привязку одного файла. Передайте
`{"fileId":"…","studentId":"…"}` для ученика из списка класса или `{"fileId":"…","newStudent":{"name":"Иванов Иван"}}`
для явного создания ученика. Ответ `200` содержит `fileId`, `studentId`, `attemptId`, `checkId` и статус `bound`.
Повтор подтверждения возвращает ту же привязку. Попытка изменить подтверждённый выбор возвращает `409`.


Одинаковое содержимое у одного ученика в рамках одного варианта сохраняет `attemptId`, даже если имя файла изменилось.
Повтор для той же ревизии варианта сохраняет `checkId`. При сдаче тех же байтов к другой ревизии остаётся
прежний `attemptId` и создаётся отдельный `checkId`. Изменение содержимого создаёт новую попытку.
Для новой попытки сохраняется время загрузки. Если в старой записи время загрузки отсутствует, используется
время создания попытки.

### Удаление варианта и сдачи

`DELETE /v1/assignments/{assignmentId}?confirm=true` удаляет из кабинета все ревизии варианта вместе со сдачами,
загрузками и результатами. `DELETE /v1/attempts/{attemptId}?confirm=true` удаляет из кабинета только выбранную
сдачу ученика и все проверки этой сдачи. `attemptId` берётся из статуса загруженного файла.

Оба запроса доступны владельцу класса. Без `confirm=true` они возвращают `409` с кодом `confirmation_required`.
Успешное удаление возвращает `204`. Исходники и история остаются в хранилище, а прямые ссылки на удалённые
результаты возвращают `404`. Повторная загрузка тех же байтов после удаления сдачи создаёт новую попытку.


### События кабинета

Кабинет сохраняет `assignment.published` при создании ревизии и `attempt.submitted` при создании
проверки в той же транзакции PostgreSQL, что и бизнес-запись. Повтор, который возвращает существующий
`checkId`, сохраняет исходное событие. В outbox хранится полный конверт `checks.v1` с `eventId`,
`traceId`, временем и ссылкой на DOCX.

Отправитель записывает конверт в поле `data` потока `checks.v1`. При повторной доставке байты конверта,
`eventId` и `traceId` сохраняются. Воркер использует `eventId` для дедупликации.


[Пример передачи работ](examples/handoff/README.md) содержит полученные через `XREAD` сообщения
и оба синтетических DOCX. Проверка `tests/infra/testChecksHandoff.py` читает документы с правами сервиса проверки.

### Статус и результат проверки

`GET /v1/checks/{checkId}` принимает `checkId` из статуса загрузки. Кабинет проверяет владельца класса
и запрашивает статус у `GET /internal/v1/checks/{checkId}` со служебным правом `checks.read`.
Чужие и неизвестные проверки возвращают тот же `404` без вызова `grading`.

Ответ `200` содержит `checkId`, `attemptId` и `state`. Варианта два, их различает наличие `taskResults`:

- `CheckPending` — результата нет: `queued`, `waiting_reference`, `running`, `failed` или `needs_review` без
  черновика (например, вариант не удалось разобрать). Есть `reason`.
- `CheckReady` — результат есть: `taskResults`, `overallReview` и текущий `resultRev`. `state` равен `ready`
  или `needs_review`, если хотя бы у одного задания `needsReview`; такие задания учитель проверяет сам.

Если проверка уже создана в кабинете, а `grading` ещё возвращает контрактный `not_found`, кабинет показывает
`queued`. Если есть `publishedResultRev`, он указывает последнюю опубликованную учителем ревизию. При отличии
номеров текущий результат относится к черновику. `markdownRef` и ключи S3 остаются во внутреннем API.

Недоступность `grading` даёт `503`, таймаут запроса — `504`, невалидный ответ или несовпадение `checkId`
либо `attemptId` — `502` с кодом `grading_invalid_response`. Кабинет передаёт `X-Trace-Id` в `grading`.

### Эталонные ответы варианта

`GET /v1/assignments/{assignmentId}/references?rev=N` проверяет владельца ревизии и возвращает тело
`GET /internal/v1/assignments/{assignmentId}/references?assignmentRev=N` (право `checks.read`, модель
`grading/assignmentreferences.py`): состояние подготовки варианта и по каждому заданию номер, условие и эталон
реестра № 3. Готовый `scalar.v1` дублируется строкой `answer`; не готовый эталон называет `state` и `reasonCode`.
Пока `grading` не получил ревизию, кабинет отвечает `state: queued` без заданий.

### Отчёт по классу

`GET /v1/assignments/{assignmentId}/report?rev=N` проверяет владельца ревизии варианта `N` и читает
`GET /internal/v1/assignments/{assignmentId}/report?assignmentRev=N` (модель `grading/assignmentreport.py`).
У внутреннего маршрута `rev` — номер сохранённого снимка отчёта, а не варианта: с `rev` он возвращает этот
снимок, без `rev` считает отчёт ревизии `assignmentRev` (по умолчанию последней) и сохраняет его снимком.
`grading` выбирает одну проверку на ученика и считает исходы по заданиям и учеников по категориям замечаний.
Кабинет переводит это в `cabinet/reports.py`: `correct`, `incorrect`, `skipped` из распределения ответов,
`issuesByCategory` — число затронутых учеников.

Кабинет добавляет список класса, несдавших и сдавших без выбранного результата. Доли делятся на число
уникальных сдавших этот вариант, а при отсутствии сдач равны `null`. `unchecked` считает сдавших без
одного из трёх исходов задания, поэтому пропуск — отдельный исход.
Если в ревизии есть удалённые сдачи, кабинет передаёт их проверки повторяемым параметром `excludeCheckId`.
Внутренний API исключает эти проверки при выборе результата ученика и подсчёте итогов. Вместе с `rev` эти
параметры дают `422`. Если `grading` ещё не получил вариант, кабинет отдаёт отчёт без результатов.

### Правки и выгрузка отзыва

`PATCH /v1/checks/{checkId}/review` принимает `baseRev`, `editId` и список типизированных `changes`
(`cabinet/reviews.py`). `POST /v1/checks/{checkId}/publish` принимает `resultRev`. После проверки владельца
кабинет передаёт их одноимённым маршрутам `/internal/v1`, добавляя `actorId` из сессии. Правки он переводит
в операции `grading/reviewedit.py`: `remove_issue` → `removeIssue`, `update_issue` → `updateIssue`,
`update_verdict` → `setVerdict`, `update_overall_review` → `setOverallReview`. Идентификатор замечания
уникален внутри задания, поэтому в `remove_issue` и `update_issue` стоит передавать `taskId`. Без него кабинет
читает ревизию `baseRev` и находит единственное задание с этим `issueId`, иначе отвечает `422`.
При конфликте `409` `grading` присылает текущую ревизию в заголовке `X-Result-Rev`, кабинет возвращает её
полем `resultRev` в конверте ошибки.

`GET /v1/checks/{checkId}/markdown?rev=N` отдаёт опубликованную ревизию `N` как `text/markdown`. Кабинет
получает файл от `GET /internal/v1/checks/{checkId}/markdown?rev=N`: `grading` читает объект из бакета
`results` и сверяет `sha256` из `markdownRef`, так что параметры S3 наружу не выходят. Неопубликованная
ревизия — `404`.
