# Сертификаты для ГигаЧата

API ГигаЧата подписано сертификатом Минцифры («Russian Trusted Root CA»), которого нет в образе `python:3.13-slim`.
Положите сюда файл корневого сертификата в формате PEM, например `russian_trusted_root_ca.pem`
(скачивается на https://www.gosuslugi.ru/crt), и укажите в `.env` в корне репозитория:

```
GIGACHAT_CONTAINER_CA_BUNDLE=/certs/russian_trusted_root_ca.pem
```

Папка монтируется только для чтения в `gateway` и `solver-builder`. Файлы сертификатов в git не попадают
(`deploy/certs/.gitignore`), в git лежит только этот README.
