# DocFlow

API для распознавания документов: принимает фото или PDF, асинхронно возвращает PDF-скан с текстовым слоем,
данные, извлечённые по схеме документа, результат проверок и вебхук. Внешние зависимости (модель, OCR, хранилище)
скрыты за протоколами, поэтому юнит-тесты идут без сети и Docker.

## Тесты

```
uv sync --all-extras
uv run pytest -m "not integration and not e2e"   # быстрые, без сервисов
docker compose up -d                               # postgres, redis, minio
uv run pytest -m integration                       # нужны ещё tesseract-ocr (+rus) и ghostscript
uv run ruff check
```

## Запуск в проде

API: `uvicorn --factory docflow.api.app:create_app`. Размер тела `POST /v1/documents` ограничен в самом приложении
(`DOCFLOW_MAX_REQUEST_MB`, по умолчанию 200), но лимит нужен и на прокси перед ним — иначе прокси сам буферизует
гигабайты: в nginx `client_max_body_size 200m;` (не больше `DOCFLOW_MAX_REQUEST_MB`).
