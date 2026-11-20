# Пример передачи работ сервису проверки

В каталоге два сообщения из поля `data` потока `checks.v1`, полученные после загрузки варианта и работы через кабинет. `variant.docx` и `submission.docx` содержат синтетические данные. SHA-256 каждого документа указан в соответствующем `docRef`.

Чтобы получить свежие сообщения на отдельном Compose-стенде, запустите проверку из корня проекта. Она читает поток через `XREAD`, разбирает сообщения функцией `grading.intake.readEvent` и открывает документы ключом сервиса проверки.

```bash
docker compose -f deploy/compose.yaml up -d --build --wait
uv run pytest tests/infra/testChecksHandoff.py
```

Чтобы воспроизвести именно сохранённые сообщения на чистом стенде, запустите следующий блок из корня проекта. Он восстановит исходные ключи `docRef` в S3 и добавит в Redis оба сохранённых конверта с прежними `eventId`. Для записи используются локальные учётные данные кабинета из окружения.

```bash
uv run python - <<'PY'
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from redis import Redis

from cabinet.documents import buildS3Client, storeDocx
from cabinet.outboxpublisher import appendToStream
from cabinet.settings import Settings
from grading.intake import readEvent

examples = Path("contracts/contracts/v1/examples/handoff")
settings = Settings.fromEnv()
pairs = [("assignment.published", "variant.docx"), ("attempt.submitted", "submission.docx")]

with closing(buildS3Client(settings)) as s3, Redis.from_url(settings.redisUrl) as redis:
    for eventType, filename in pairs:
        data = (examples / f"{eventType}.json").read_text(encoding="utf-8")
        ref = readEvent(data).payload.docRef
        docx = (examples / filename).read_bytes()
        assert sha256(docx).hexdigest() == ref.sha256
        assert storeDocx(s3, ref.key, docx).bucket == ref.bucket
        appendToStream(redis, data)
PY
```
