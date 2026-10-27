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
