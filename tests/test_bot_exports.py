"""Ответы и уведомления в бот через очередь выгрузок: кому, что, повторы, отключённый бот, токен в ошибках."""

import sys
import uuid
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from test_pipeline import INVOICE, make_deps, make_doc

from docflow.api.app import create_app
from docflow.channels.bot_messages import failed_text, result_text
from docflow.config import Settings
from docflow.db.models import ApiKey, BotChat, Document, Export, FieldLabels, Tenant, TenantBot, generate_api_key
from docflow.exports import (
    RETRY_DELAYS,
    build_export_doc,
    deliver_export,
    enqueue_bot_messages,
    requeue_stale_exports,
)
from docflow.extraction.fake import FakeExtractor
from docflow.pipeline import SchemaResolver, process_document
from docflow.queue import DELIVER_EXPORT, ListEnqueuer
from docflow.schemas.library import builtin_schemas
from docflow.secrets import encrypt_secrets
from docflow.storage.blobs import LocalBlobStore

sys.path.insert(0, str(Path(__file__).parent / "channels"))
from fake_telegram import FakeTelegram  # noqa: E402

TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
KEY = Fernet.generate_key().decode()
SITE = "https://docflow.example.test"
SOURCE_CHAT = 500
OTHER_CHAT = 600
MESSAGE_ID = 77
PREFIX = "Отправка в Telegram: "
DATA = {"number": "15", "issued": "2026-10-20", "total": 250.0, "supplier": "ООО «Ромашка»"}
ISSUES = [
    {"field": "total", "code": "uncertain", "message": "первая причина"},
    {"field": "supplier", "code": "uncertain", "message": "вторая причина"},
]
MODEL_ERROR = "Не удалось извлечь данные (api_error: 401 invalid x-api-key)"


class Env:
    def __init__(self, sm, tmp_path, settings):
        self.sm = sm
        self.tg = FakeTelegram()
        self.http = httpx.AsyncClient(transport=self.tg.transport)
        self.enq = ListEnqueuer()
        self.blobs = LocalBlobStore(tmp_path / "blobs")
        self.settings = settings
        self.tenant_id = None
        self.bot_id = None
        app = create_app(settings, sessionmaker=sm, blobs=self.blobs, enqueuer=self.enq)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def add_bot(self, *, active=True, token_key=KEY) -> uuid.UUID:
        async with self.sm() as s:
            if self.tenant_id is None:
                tenant = Tenant(name="Ромашка")
                s.add(tenant)
                await s.flush()
                self.tenant_id = tenant.id
            bot = TenantBot(
                tenant_id=self.tenant_id, token_enc=encrypt_secrets({"token": TOKEN}, token_key),
                token_hash="h", username="acme_bot", invite_code="code", active=active,
            )
            s.add(bot)
            await s.commit()
            self.bot_id = bot.id
            return bot.id

    async def add_chat(self, chat_id=SOURCE_CHAT, *, all_documents=True) -> uuid.UUID:
        async with self.sm() as s:
            chat = BotChat(bot_id=self.bot_id, chat_id=chat_id, title="Чат", all_documents=all_documents)
            s.add(chat)
            await s.commit()
            return chat.id

    def source_ref(self, chat_id=SOURCE_CHAT, bot_id=None) -> dict:
        bot = str(bot_id or self.bot_id)
        return {"bot_id": bot, "chat_id": chat_id, "message_id": MESSAGE_ID, "media_group_id": None}

    async def add_doc(self, *, telegram=True, status="ok", slug="invoice_ru", version=1, **values) -> uuid.UUID:
        if telegram:
            values.setdefault("source", "telegram")
            values.setdefault("source_ref", self.source_ref())
        values.setdefault("data", DATA)
        async with self.sm() as s:
            doc = Document(
                tenant_id=self.tenant_id, schema_slug=slug, schema_version=version, status=status,
                files=[], mode="gray", pages=1, **values,
            )
            s.add(doc)
            await s.commit()
            return doc.id

    async def enqueue(self, doc_id, purpose="bot_ready") -> int:
        async with self.sm() as s:
            return await enqueue_bot_messages(s, await s.get(Document, doc_id), purpose, self.enq)

    async def exports(self, doc_id) -> list[Export]:
        async with self.sm() as s:
            q = select(Export).where(Export.document_id == doc_id).order_by(Export.created_at, Export.id)
            return list((await s.execute(q)).scalars().all())

    async def bot(self) -> TenantBot:
        async with self.sm() as s:
            return await s.get(TenantBot, self.bot_id)

    async def deliver(self, export_id, expected_attempt=None) -> Export:
        async with self.sm() as s:
            await deliver_export(
                s, export_id, http=self.http, enqueuer=self.enq, blobs=self.blobs, schemas=SchemaResolver(),
                settings=self.settings, expected_attempt=expected_attempt,
            )
        async with self.sm() as s:
            return await s.get(Export, export_id)

    async def send_all(self, doc_id) -> None:
        for export in await self.exports(doc_id):
            if export.status == "pending":
                await self.deliver(export.id)

    def texts(self) -> list[dict]:
        return [payload for method, payload in self.tg.calls if method == "sendMessage"]

    def documents(self) -> list[dict[str, bytes]]:
        return [_parts(payload) for method, payload in self.tg.calls if method == "sendDocument"]

    async def headers(self) -> dict[str, str]:
        async with self.sm() as s:
            plain, prefix, key_hash = generate_api_key()
            s.add(ApiKey(tenant_id=self.tenant_id, prefix=prefix, key_hash=key_hash))
            await s.commit()
        return {"Authorization": f"Bearer {plain}"}


def _parts(payload: dict) -> dict[str, bytes]:
    """multipart из заглушки Telegram: имя поля → байты."""
    raw = f"Content-Type: {payload['content_type']}\r\n\r\n".encode() + payload["multipart"]
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in message_from_bytes(raw).get_payload()
    }


def _caption(parts: dict[str, bytes]) -> str:
    return parts["caption"].decode()


@pytest.fixture
async def env(digital_font, sm, tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:", secrets_key=KEY, public_base_url="https://api.example.test",
        public_site_url=SITE, webhook_allow_private=False,
    )
    e = Env(sm, tmp_path, settings)
    yield e
    await e.http.aclose()
    await e.client.aclose()


async def linked(env) -> uuid.UUID:
    await env.add_bot()
    return await env.add_chat()


# --- ответ в исходный чат ---------------------------------------------------


async def test_ok_reply_pdf_summary_reply_to(env):
    await linked(env)
    async with env.sm() as s:
        s.add(
            FieldLabels(
                tenant_id=env.tenant_id, schema_slug="invoice_ru", labels={"total": "К оплате"},
                queue_fields=["number", "total", "supplier"],
            )
        )
        await s.commit()
    doc_id = await env.add_doc()
    assert await env.enqueue(doc_id) == 1
    await env.send_all(doc_id)

    assert env.texts() == []
    [parts] = env.documents()
    assert parts["chat_id"] == str(SOURCE_CHAT).encode()
    assert b'"message_id": 77' in parts["reply_parameters"]
    assert parts["document"].startswith(b"%PDF")
    caption = _caption(parts)
    assert "№ 15" in caption and "от 20.10.2026" in caption
    assert "К оплате: 250,00 ₽" in caption
    assert "ООО «Ромашка»" in caption
    assert f"Открыть в кабинете: {SITE}/app/documents/{doc_id}" in caption
    # номер и дата уже в заголовке, отдельными строками не повторяются
    middle = caption.split("\n", 1)[1].split("Открыть")[0]
    assert "15" not in middle and "2026" not in middle
    assert (await env.exports(doc_id))[0].status == "done"


async def test_needs_review_reply_first_reason_and_pdf(env):
    await linked(env)
    doc_id = await env.add_doc(status="needs_review", issues=ISSUES)
    await env.enqueue(doc_id)
    await env.send_all(doc_id)

    assert env.texts() == []
    [parts] = env.documents()
    caption = _caption(parts)
    assert caption.startswith("Нужна проверка: ")
    assert "первая причина" in caption and "вторая причина" not in caption
    assert f"{SITE}/app/documents/{doc_id}" in caption
    assert parts["document"].startswith(b"%PDF")


async def test_failed_reply_text_only(env):
    await linked(env)
    doc_id = await env.add_doc(status="failed", error="Исходный файл не найден", data={})
    await env.enqueue(doc_id)
    await env.send_all(doc_id)

    assert env.documents() == []
    [msg] = env.texts()
    assert msg["text"].startswith("Не удалось распознать документ: Исходный файл не найден")
    assert f"{SITE}/app/documents/{doc_id}" in msg["text"]
    assert msg["reply_parameters"]["message_id"] == MESSAGE_ID


async def test_failed_auto_no_schema_no_crash(env):
    await linked(env)
    doc_id = await env.add_doc(
        status="failed", slug="auto", version=0, error="Не удалось определить тип документа", data={}
    )
    await env.enqueue(doc_id)
    await env.send_all(doc_id)

    [msg] = env.texts()
    assert "Не удалось определить тип документа" in msg["text"]
    assert msg["reply_parameters"]["message_id"] == MESSAGE_ID
    assert (await env.exports(doc_id))[0].status == "done"


async def test_deleted_schema_sends_text_without_crash(env):
    await linked(env)
    doc_id = await env.add_doc(slug="gone_type")
    await env.enqueue(doc_id)
    await env.send_all(doc_id)

    assert env.documents() == []
    [msg] = env.texts()
    assert msg["text"] == "Документ распознан"
    assert (await env.exports(doc_id))[0].status == "done"


async def test_failed_reply_hides_raw_model_error(env):
    await linked(env)
    doc_id = await env.add_doc(status="failed", error=MODEL_ERROR, data={})
    await env.enqueue(doc_id)
    await env.send_all(doc_id)

    [msg] = env.texts()
    assert "модель не ответила. Попробуйте прислать его ещё раз" in msg["text"]
    assert "api_error" not in msg["text"] and "x-api-key" not in msg["text"]


async def test_pdf_render_failure_falls_back_to_text(env, monkeypatch):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)

    def boom(*_a, **_k):
        raise RuntimeError("слишком длинные значения")

    monkeypatch.setattr("docflow.exports.render_digital_pdf", boom)
    await env.send_all(doc_id)

    assert env.documents() == []
    [msg] = env.texts()
    assert f"Открыть в кабинете: {SITE}/app/documents/{doc_id}" in msg["text"]
    assert msg["reply_parameters"]["message_id"] == MESSAGE_ID
    assert (await env.exports(doc_id))[0].status == "done"


async def test_caption_within_limit_keeps_link(env):
    await linked(env)
    doc_id = await env.add_doc(data={**DATA, "supplier": "Я" * 2000})
    async with env.sm() as s:
        document = await s.get(Document, doc_id)
    doc = build_export_doc(document, builtin_schemas()["invoice_ru"], env.settings, env.blobs)
    caption = result_text(doc, ["supplier", "total"])
    assert len(caption) <= 1024
    assert caption.endswith(f"Открыть в кабинете: {SITE}/app/documents/{doc_id}")


def test_failed_text_without_error_and_document():
    assert failed_text(None, None) == "Не удалось распознать документ"


async def test_reviewed_message_same_chat(env):
    await linked(env)
    headers = await env.headers()
    approve = await env.add_doc(status="needs_review", issues=ISSUES)
    reject = await env.add_doc(status="needs_review", issues=ISSUES)

    r = await env.client.post(f"/v1/documents/{approve}/review", headers=headers, json={"decision": "approve"})
    assert r.status_code == 200, r.text
    r = await env.client.post(f"/v1/documents/{reject}/review", headers=headers, json={"decision": "reject"})
    assert r.status_code == 200, r.text
    await env.send_all(approve)
    await env.send_all(reject)

    assert env.documents() == []
    first, second = env.texts()
    assert first["text"].startswith("Документ проверен и подтверждён: ")
    assert second["text"].startswith("Документ отклонён: ")
    for msg in (first, second):
        assert msg["chat_id"] == SOURCE_CHAT
        assert msg["reply_parameters"]["message_id"] == MESSAGE_ID


async def test_review_survives_bot_enqueue_failure(env, monkeypatch):
    await linked(env)
    headers = await env.headers()
    doc_id = await env.add_doc(status="needs_review", issues=ISSUES)

    async def boom(*_a, **_k):
        raise RuntimeError("redis")

    monkeypatch.setattr("docflow.api.documents.enqueue_bot_messages", boom)
    r = await env.client.post(f"/v1/documents/{doc_id}/review", headers=headers, json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "ok"


# --- кому уходит ------------------------------------------------------------


async def test_other_channel_notifies_only_flagged_chats(env):
    await env.add_bot()
    await env.add_chat(SOURCE_CHAT, all_documents=True)
    await env.add_chat(OTHER_CHAT, all_documents=False)
    doc_id = await env.add_doc(telegram=False)
    assert await env.enqueue(doc_id) == 1
    await env.send_all(doc_id)

    assert env.documents() == []
    [msg] = env.texts()
    assert msg["chat_id"] == SOURCE_CHAT
    assert msg["text"].startswith("Новый документ: ") and "reply_parameters" not in msg


@pytest.mark.parametrize(
    ("status", "values", "start"),
    [
        ("needs_review", {"issues": ISSUES}, "Нужна проверка: "),
        ("failed", {"error": MODEL_ERROR}, "Не удалось распознать документ: модель не ответила"),
    ],
)
async def test_other_channel_texts(env, status, values, start):
    await linked(env)
    doc_id = await env.add_doc(telegram=False, status=status, **values)
    await env.enqueue(doc_id)
    await env.send_all(doc_id)

    assert env.documents() == []
    [msg] = env.texts()
    assert msg["text"].startswith(start) and "reply_parameters" not in msg


async def test_telegram_doc_not_broadcast(env):
    await env.add_bot()
    await env.add_chat(SOURCE_CHAT, all_documents=False)
    await env.add_chat(OTHER_CHAT, all_documents=True)
    doc_id = await env.add_doc()
    assert await env.enqueue(doc_id) == 1
    await env.send_all(doc_id)

    [parts] = env.documents()
    assert parts["chat_id"] == str(SOURCE_CHAT).encode()
    assert env.texts() == []


async def test_telegram_doc_of_previous_bot_is_other_channel(env):
    await env.add_bot()
    await env.add_chat(OTHER_CHAT, all_documents=True)
    doc_id = await env.add_doc(source_ref=env.source_ref(bot_id=uuid.uuid4()))
    assert await env.enqueue(doc_id) == 1
    await env.send_all(doc_id)

    [msg] = env.texts()
    assert msg["chat_id"] == OTHER_CHAT and "reply_parameters" not in msg


async def test_source_chat_gone_nothing_queued(env):
    await env.add_bot()
    await env.add_chat(OTHER_CHAT)
    doc_id = await env.add_doc()
    assert await env.enqueue(doc_id) == 0
    assert await env.exports(doc_id) == []


async def test_no_bot_or_inactive_nothing_queued(env):
    await env.add_bot(active=False)
    await env.add_chat()
    doc_id = await env.add_doc()
    assert await env.enqueue(doc_id) == 0

    async with env.sm() as s:
        tenant = Tenant(name="без бота")
        s.add(tenant)
        await s.commit()
    env.tenant_id = tenant.id
    doc_id = await env.add_doc(telegram=False)
    assert await env.enqueue(doc_id) == 0
    assert env.enq.calls == []


async def test_enqueue_twice_no_duplicate(env):
    await linked(env)
    doc_id = await env.add_doc()
    assert await env.enqueue(doc_id) == 1
    assert await env.enqueue(doc_id) == 0

    [export] = await env.exports(doc_id)
    assert export.destination_id is None and export.purpose == "bot_ready"
    assert [c[0] for c in env.enq.calls] == [DELIVER_EXPORT]


# --- повторы и состояние бота -----------------------------------------------


async def test_retry_on_429_then_done(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    env.tg.fail_with["sendDocument"] = 429
    before = datetime.now(UTC)

    after = await env.deliver(export.id, expected_attempt=0)
    assert after.status == "pending" and after.attempt == 1
    delay = (after.next_attempt_at - before).total_seconds()
    assert RETRY_DELAYS[0] - 1 < delay < RETRY_DELAYS[0] + 5
    assert "429" in after.last_error
    bot = await env.bot()
    assert bot.last_error.startswith(PREFIX) and "429" in bot.last_error and bot.active is True
    assert len(env.enq.calls) == 2  # первая постановка и повтор

    env.tg.fail_with.clear()
    after = await env.deliver(export.id, expected_attempt=1)
    assert after.status == "done" and after.last_error is None and after.next_attempt_at is None
    assert (await env.bot()).last_error is None
    # два обращения: отказ и один успешный вызов
    assert [m for m, _ in env.tg.calls] == ["sendDocument", "sendDocument"]


@pytest.mark.parametrize("foreign", ["Опрос Telegram: Telegram getUpdates: 500", "токен больше не действует"])
async def test_success_keeps_foreign_last_error(env, foreign):
    await linked(env)
    async with env.sm() as s:
        (await s.get(TenantBot, env.bot_id)).last_error = foreign
        await s.commit()
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    await env.send_all(doc_id)

    assert (await env.exports(doc_id))[0].status == "done"
    assert (await env.bot()).last_error == foreign


async def test_revoked_401_marks_bot_inactive(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    env.tg.fail_with["*"] = 401
    queued = len(env.enq.calls)

    after = await env.deliver(export.id)
    assert after.status == "failed" and after.next_attempt_at is None
    assert after.last_error == "токен больше не действует"
    bot = await env.bot()
    assert bot.active is False and bot.last_error == "токен больше не действует"
    assert len(env.enq.calls) == queued


async def test_permanent_telegram_error_no_retry(env):
    await linked(env)
    doc_id = await env.add_doc(status="failed", error="x", data={})
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    env.tg.fail_with["*"] = (403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})
    queued = len(env.enq.calls)

    after = await env.deliver(export.id)
    assert after.status == "failed" and "blocked" in after.last_error
    bot = await env.bot()
    assert bot.active is True and bot.last_error.startswith(PREFIX)
    assert len(env.enq.calls) == queued


async def test_attempts_exhausted_fails(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    async with env.sm() as s:
        (await s.get(Export, export.id)).attempt = len(RETRY_DELAYS) + 1
        await s.commit()

    after = await env.deliver(export.id)
    assert after.status == "failed" and after.last_error.startswith("превышено число попыток")
    assert env.tg.calls == []


async def test_secrets_key_missing_is_permanent(env):
    await env.add_bot(token_key=Fernet.generate_key().decode())
    await env.add_chat()
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    queued = len(env.enq.calls)

    after = await env.deliver(export.id)
    assert after.status == "failed" and "ключ шифрования" in after.last_error
    assert env.tg.calls == [] and len(env.enq.calls) == queued
    assert (await env.bot()).last_error.startswith(PREFIX)


async def test_unlinked_chat_skipped(env):
    chat_id = await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    async with env.sm() as s:
        await s.delete(await s.get(BotChat, chat_id))
        await s.commit()

    after = await env.deliver(export.id)
    assert after.status == "skipped" and after.last_error == "чат отвязан"
    assert env.tg.calls == []


async def test_disconnected_bot_skipped(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    async with env.sm() as s:
        (await s.get(TenantBot, env.bot_id)).active = False
        await s.commit()

    after = await env.deliver(export.id)
    assert after.status == "skipped" and after.last_error == "бот отключён"
    assert env.tg.calls == []


async def test_document_back_in_queue_skipped(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    async with env.sm() as s:
        (await s.get(Document, doc_id)).status = "queued"
        await s.commit()

    after = await env.deliver(export.id)
    assert after.status == "skipped"
    assert env.tg.calls == []


async def test_export_errors_have_no_token(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    env.tg.fail_with["*"] = httpx.ConnectError(f"https://api.telegram.org/bot{TOKEN}/sendDocument")

    after = await env.deliver(export.id)
    assert after.status == "pending"
    assert TOKEN not in after.last_error and "ConnectError" in after.last_error
    assert TOKEN not in (await env.bot()).last_error


async def test_no_connection_held_during_send(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    pool = env.sm.kw["bind"].sync_engine.pool
    seen = []
    env.tg.on_call = lambda _method: seen.append(pool.checkedout())

    await env.deliver(export.id)
    assert seen == [0]


async def test_requeue_stale_picks_bot_exports(env):
    await linked(env)
    doc_id = await env.add_doc()
    await env.enqueue(doc_id)
    [export] = await env.exports(doc_id)
    env.enq.calls.clear()  # задача потеряна: Redis упал после commit

    async with env.sm() as s:
        assert await requeue_stale_exports(s, env.enq, datetime.now(UTC) + timedelta(minutes=10)) == 1
    assert [c[0] for c in env.enq.calls] == [DELIVER_EXPORT]
    assert env.enq.calls[0][1] == (str(export.id), 0)


# --- повторное распознавание ------------------------------------------------


async def test_retype_resends_bot_reply(env, tmp_path):
    fake = FakeExtractor({"invoice_ru": INVOICE, "_classify": None})
    deps = make_deps(env.sm, tmp_path, extractor=fake)
    deps.blobs, deps.enqueuer = env.blobs, env.enq
    doc_id = await make_doc(deps, slug="auto", version=0)
    async with env.sm() as s:
        env.tenant_id = (await s.get(Document, doc_id)).tenant_id
    await linked(env)
    async with env.sm() as s:
        document = await s.get(Document, doc_id)
        document.source, document.source_ref = "telegram", env.source_ref()
        await s.commit()

    await process_document(deps, doc_id)
    await env.send_all(doc_id)
    [first] = env.texts()
    assert "Не удалось определить тип документа" in first["text"]
    assert [e.status for e in await env.exports(doc_id)] == ["done"]

    r = await env.client.post(
        f"/v1/documents/{doc_id}/retype", headers=await env.headers(), json={"schema": "invoice_ru"}
    )
    assert r.status_code == 202, r.text
    await process_document(deps, doc_id)
    await env.send_all(doc_id)

    assert len(env.texts()) == 1
    [parts] = env.documents()
    assert parts["chat_id"] == str(SOURCE_CHAT).encode()
    assert [e.status for e in await env.exports(doc_id)] == ["done"]
