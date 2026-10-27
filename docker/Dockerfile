# Один Dockerfile, три цели:
#   api    — FastAPI + миграции и CLI оператора (без OCR)
#   worker — то же + tesseract (rus, eng) и ghostscript для текстового слоя
#   test   — worker + dev-зависимости и tests/: интеграционные тесты OCR внутри контейнера
# Сборка из корня репозитория: docker build -f docker/Dockerfile --target worker .

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN useradd --system --uid 10001 --create-home --home-dir /home/docflow docflow

# зависимости отдельным слоем: правка кода не пересобирает их
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable


FROM base AS api
USER docflow
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["uvicorn", "docflow.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS worker
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng ghostscript pngquant \
    && rm -rf /var/lib/apt/lists/*
USER docflow
CMD ["arq", "docflow.worker.WorkerSettings"]


FROM worker AS test
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --extra dev --no-editable
COPY tests ./tests
USER docflow
CMD ["pytest", "-p", "no:cacheprovider", "-m", "integration", "-v"]
