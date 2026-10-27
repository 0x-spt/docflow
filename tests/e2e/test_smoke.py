"""Сквозной прогон против живого стека: реальные API, очередь, S3, OCR в воркере; экстрактор — заготовка.

    docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
    uv run pytest -m e2e -v
"""

import hashlib
import hmac
import io
import json
import queue
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
API = "http://localhost:8000"
POLL_TIMEOUT = 60
WEBHOOK_TIMEOUT = 30
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "DejaVuSans.ttf",
]


def _api_up() -> bool:
    try:
        return httpx.get(f"{API}/healthz", timeout=2, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module")
def client():
    if not _api_up():
        pytest.skip(f"API недоступен на {API}/healthz — поднимите стек docker compose (см. docstring)")
    with httpx.Client(base_url=API, timeout=30, trust_env=False) as c:
        yield c


def create_tenant(name: str) -> str:
    """`docflow create-tenant` внутри контейнера api; возвращает выданный ключ."""
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "docflow", "create-tenant", name],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    m = re.search(r"API-ключ:\s*(\S+)", out.stdout)
    assert m, f"ключ не найден в выводе: {out.stdout!r}"
    return m.group(1)


class _Receiver(BaseHTTPRequestHandler):
    received: queue.Queue

    def do_POST(self):  # noqa: N802 — имя задаёт http.server
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.received.put((self.path, dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    """Приёмник вебхуков на хосте: 0.0.0.0, чтобы до него дотянулся контейнер через host.docker.internal."""
    handler = type("Receiver", (_Receiver,), {"received": queue.Queue()})
    server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], handler.received
    server.shutdown()
    server.server_close()


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    pytest.skip("не найден TrueType-шрифт с кириллицей")


def invoice_photo() -> bytes:
    """Синтетическое «фото» счёта: белый лист с крупным русским текстом на сером столе."""
    sheet = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((120, 140), "СЧЁТ НА ОПЛАТУ № СЧ-0042", font=_font(64), fill="black")
    body = _font(40)
    lines = [
        "от 1 октября 2026 г.",
        "Поставщик: ООО «Ромашка», ИНН 7707083893",
        "Покупатель: ООО «Василёк»",
        "Бумага офисная А4 — 10 пач. × 350 = 3500",
        "Доставка — 1 усл. × 500 = 500",
        "Итого: 4000    НДС: 800",
        "Всего к оплате: 4800 руб.",
    ]
    for i, line in enumerate(lines):
        draw.text((120, 320 + i * 90), line, font=body, fill="black")
    photo = Image.new("RGB", (1500, 2000), (90, 90, 95))
    photo.paste(sheet, (130, 120))
    buf = io.BytesIO()
    photo.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def wait_done(client: httpx.Client, headers: dict, doc_id: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        doc = client.get(f"/v1/documents/{doc_id}", headers=headers).raise_for_status().json()
        if doc["status"] in ("ok", "needs_review", "failed"):
            return doc
        if time.monotonic() > deadline:
            pytest.fail(f"документ не обработан за {POLL_TIMEOUT} с: {doc}")
        time.sleep(1)


def test_upload_ocr_extract_and_signed_webhook(client, receiver):
    port, received = receiver
    key = create_tenant("e2e")
    headers = {"Authorization": f"Bearer {key}"}

    hook = client.post(
        "/v1/webhooks",
        headers=headers,
        json={"url": f"http://host.docker.internal:{port}/hook", "events": ["document.completed"]},
    )
    assert hook.status_code == 201, hook.text
    secret = hook.json()["secret"]

    up = client.post(
        "/v1/documents",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        data={"schema": "invoice_ru"},
        files=[("files", ("invoice.jpg", invoice_photo(), "image/jpeg"))],
    )
    assert up.status_code == 202, up.text
    doc_id = up.json()["id"]

    doc = wait_done(client, headers, doc_id)
    assert doc["status"] == "ok", doc
    assert doc["data"]["supplier_inn"] == "7707083893"
    assert doc["pages"] == 1

    # текстовый слой — от настоящего tesseract в воркере, а не от экстрактора-заготовки
    pdf = client.get(f"/v1/documents/{doc_id}/pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    text = pdfium.PdfDocument(pdf.content)[0].get_textpage().get_text_range().upper()
    assert "СЧЕТ" in text or "СЧЁТ" in text, f"OCR не нашёл заголовок, текстовый слой: {text[:300]!r}"

    try:
        path, hook_headers, body = received.get(timeout=WEBHOOK_TIMEOUT)
    except queue.Empty:
        pytest.fail(f"вебхук не пришёл за {WEBHOOK_TIMEOUT} с")
    assert path == "/hook"
    lower = {k.lower(): v for k, v in hook_headers.items()}
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(lower["x-docflow-signature"], expected)
    event = json.loads(body)
    assert event["event"] == "document.completed"
    assert lower["x-docflow-event"] == "document.completed"
    assert event["document"]["id"] == doc_id
    assert event["document"]["status"] == "ok"
