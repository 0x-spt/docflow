import hashlib
import imaplib
import logging
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, update

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
from api_helpers import pdf_bytes, png_bytes  # noqa: E402

from docflow.channels import mailbox as mailbox_module  # noqa: E402
from docflow.channels.deps import ChannelDeps  # noqa: E402
from docflow.channels.mailbox import (  # noqa: E402
    AUTH_ERROR,
    MAX_LETTERS_PER_PASS,
    NETWORK_ERROR,
    MailboxAuthError,
    MailboxCreds,
    MailboxLetterError,
    MailboxNetworkError,
    MailboxPoller,
    check_host,
    open_imap,
    parse_letter,
    sender_allowed,
    subject_for_match,
)
from docflow.config import Settings  # noqa: E402
from docflow.db.models import Document, Export, Mailbox, Tenant  # noqa: E402
from docflow.intake import IntakeDeps  # noqa: E402
from docflow.pipeline import SchemaResolver  # noqa: E402
from docflow.queue import ListEnqueuer  # noqa: E402
from docflow.secrets import encrypt_secrets  # noqa: E402
from docflow.storage.blobs import LocalBlobStore  # noqa: E402

KEY = Fernet.generate_key().decode()
PASSWORD = "synthetic-mail-pass-1"
SENDER = "buh@firma.example"


def letter(
    subject: str = "Документы",
    sender: str = SENDER,
    pdfs: tuple[bytes, ...] = (),
    images: tuple[bytes, ...] = (),
    message_id: str | None = "<m1@firma.example>",
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "in@acme.example"
    msg["Subject"] = subject.replace("\r\n", " ")
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content("Текст письма")
    for n, data in enumerate(pdfs):
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=f"doc{n}.pdf")
    for n, data in enumerate(images):
        msg.add_attachment(data, maintype="image", subtype="png", filename=f"page{n}.png")
    return msg.as_bytes()


class FakeMail:
    """MailSession в памяти: письма живут между проходами, как на настоящем сервере."""

    def __init__(self):
        self.letters: dict[str, bytes] = {}
        self.sizes: dict[str, int] = {}
        self.seen: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], Exception] = {}  # (метод, uid) → исключение
        self.closed = 0

    def add(self, raw: bytes, size: int | None = None) -> str:
        uid = str(len(self.letters) + 1)
        self.letters[uid] = raw
        self.sizes[uid] = len(raw) if size is None else size
        return uid

    def _step(self, name: str, uid: str) -> None:
        self.calls.append((name, uid))
        if (name, uid) in self.fail:
            raise self.fail[(name, uid)]

    def called(self, name: str) -> list[str]:
        return [uid for method, uid in self.calls if method == name]

    def unseen_uids(self) -> list[str]:
        return [uid for uid in self.letters if uid not in self.seen]

    def size(self, uid: str) -> int:
        self._step("size", uid)
        return self.sizes[uid]

    def subject(self, uid: str) -> str:
        self._step("subject", uid)
        return parse_letter(self.letters[uid]).subject

    def fetch(self, uid: str) -> bytes:
        self._step("fetch", uid)
        return self.letters[uid]

    def mark_seen(self, uid: str) -> None:
        self._step("mark_seen", uid)
        self.seen.add(uid)

    def close(self) -> None:
        self.closed += 1


class FlakyBlobs(LocalBlobStore):
    broken = False

    async def put(self, key, data, content_type):
        if self.broken:
            raise OSError("диск недоступен")
        return await super().put(key, data, content_type)


class Env:
    def __init__(self, sm, tmp_path):
        self.sm = sm
        self.mail = FakeMail()
        self.connects = 0
        self.connect_error: Exception | None = None
        self.settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:", secrets_key=KEY, max_file_mb=20, webhook_allow_private=True
        )
        self.blobs = FlakyBlobs(tmp_path / "blobs")
        self.enqueuer = ListEnqueuer()
        intake = IntakeDeps(settings=self.settings, blobs=self.blobs, enqueuer=self.enqueuer, resolver=SchemaResolver())
        self.deps = ChannelDeps(sessionmaker=sm, intake=intake, settings=self.settings)
        self.tenant_id = None
        self.box_id = None
        self.poller = MailboxPoller(self.deps, connect=self.connect)

    def connect(self, creds: MailboxCreds) -> FakeMail:
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error
        return self.mail

    async def poll(self) -> None:
        await self.poller.poll(self.box_id)

    async def docs(self) -> list[Document]:
        async with self.sm() as s:
            return list((await s.execute(select(Document))).scalars())

    async def by_attachment(self) -> dict[str, Document]:
        return {d.source_ref["attachment"]: d for d in await self.docs()}

    async def box(self) -> Mailbox:
        async with self.sm() as s:
            return await s.get(Mailbox, self.box_id)

    async def set_box(self, **values) -> None:
        async with self.sm() as s:
            await s.execute(update(Mailbox).where(Mailbox.id == self.box_id).values(**values))
            await s.commit()


@pytest.fixture
async def env(sm, tmp_path):
    e = Env(sm, tmp_path)
    async with sm() as s:
        tenant = Tenant(name="Ромашка")
        s.add(tenant)
        await s.flush()
        box = Mailbox(
            tenant_id=tenant.id,
            host="imap.example.test",
            port=993,
            username="in@acme.example",
            password_enc=encrypt_secrets({"password": PASSWORD}, KEY),
            folder="INBOX",
            allowed_senders=[],
        )
        s.add(box)
        await s.commit()
        e.tenant_id, e.box_id = tenant.id, box.id
    return e


# --- письмо → документы ---------------------------------------------------


async def test_pdf_and_images_split(env):
    first, second = png_bytes((10, 10)), png_bytes((20, 20))
    uid = env.mail.add(letter(pdfs=(pdf_bytes(2), pdf_bytes(3)), images=(first, second)))
    await env.poll()
    docs = await env.by_attachment()
    assert set(docs) == {"pdf:0", "pdf:1", "images"}
    assert docs["pdf:0"].pages == 2 and docs["pdf:1"].pages == 3
    images = docs["images"]
    assert images.pages == 2
    assert [Path(f).name for f in images.files] == ["1_page0.png", "2_page1.png"]
    assert await env.blobs.get(images.files[0]) == first
    assert await env.blobs.get(images.files[1]) == second
    assert all(d.source == "email" for d in docs.values())
    assert env.mail.seen == {uid}
    box = await env.box()
    assert box.last_error is None and box.last_checked_at is not None
    assert env.mail.closed == 1


async def test_inline_logo_ignored(env):
    msg = EmailMessage()
    msg["From"] = SENDER
    msg["Subject"] = "Подпись с логотипом"
    msg["Message-ID"] = "<logo@firma.example>"
    msg.set_content("x")
    msg.add_attachment(
        png_bytes(), maintype="image", subtype="png", filename="logo.png", disposition="inline", cid="<logo@x>"
    )
    env.mail.add(msg.as_bytes())
    await env.poll()
    assert await env.docs() == []
    assert env.mail.called("mark_seen") == []

    msg.add_attachment(png_bytes(), maintype="image", subtype="png", filename="scan.png")
    assert [a.name for a in parse_letter(msg.as_bytes()).images] == ["scan.png"]


async def test_subject_selects_type(env):
    env.mail.add(letter(subject="счет на оплату", pdfs=(pdf_bytes(1),)))
    await env.poll()
    assert (await env.docs())[0].schema_slug == "invoice_ru"


async def test_forwarded_subject_selects_type(env):
    env.mail.add(letter(subject="Fwd: Счёт на оплату", pdfs=(pdf_bytes(1),)))
    await env.poll()
    assert (await env.docs())[0].schema_slug == "invoice_ru"


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Re: Счёт", "Счёт"),
        ("FWD: Счёт", "Счёт"),
        ("Fw: Re: Счёт", "Счёт"),
        ("Ответ: Счёт", "Счёт"),
        ("ПЕРЕСЛ: Счёт", "Счёт"),
        ("Счёт: апрель", "Счёт: апрель"),
        ("Reference: Счёт", "Reference: Счёт"),
    ],
)
def test_subject_for_match(subject, expected):
    assert subject_for_match(subject) == expected


async def test_default_schema_and_auto(env):
    env.mail.add(letter(subject="непонятно", pdfs=(pdf_bytes(1),), message_id="<a@x>"))
    await env.poll()
    assert (await env.docs())[0].schema_slug == "auto"
    await env.set_box(default_schema="invoice_ru")
    env.mail.add(letter(subject="непонятно", pdfs=(pdf_bytes(1),), message_id="<b@x>"))
    await env.poll()
    assert sorted(d.schema_slug for d in await env.docs()) == ["auto", "invoice_ru"]


async def test_no_attachments_left_unread_and_not_refetched(env):
    env.mail.add(letter())
    await env.poll()
    await env.poll()
    assert env.mail.called("fetch") == ["1"]
    assert env.mail.called("mark_seen") == []
    assert await env.docs() == []


async def test_foreign_sender_left_unread(env):
    await env.set_box(allowed_senders=["@firma.example"])
    env.mail.add(letter(sender="spam@other.example", pdfs=(pdf_bytes(1),)))
    await env.poll()
    await env.poll()
    assert await env.docs() == []
    assert env.mail.called("mark_seen") == []
    assert env.mail.called("fetch") == ["1"]


async def test_allowed_domain_and_address(env):
    await env.set_box(allowed_senders=["@firma.example", "boss@other.example"])
    env.mail.add(letter(sender="Иван <IVAN@Firma.example>", pdfs=(pdf_bytes(1),), message_id="<a@x>"))
    env.mail.add(letter(sender="boss@other.example", pdfs=(pdf_bytes(1),), message_id="<b@x>"))
    env.mail.add(letter(sender="clerk@other.example", pdfs=(pdf_bytes(1),), message_id="<c@x>"))
    await env.poll()
    assert len(await env.docs()) == 2
    assert env.mail.seen == {"1", "2"}


def test_sender_allowed_rules():
    assert sender_allowed("a@b.ru", [])
    assert sender_allowed("", [])
    assert sender_allowed("A@B.RU", ["a@b.ru"])
    assert sender_allowed("x@b.ru", ["@B.ru"])
    assert not sender_allowed("x@sub.b.ru", ["@b.ru"])
    assert not sender_allowed("x@evilb.ru", ["@b.ru"])
    assert not sender_allowed("a@b.ru.evil.example", ["a@b.ru", "@b.ru"])
    assert not sender_allowed("", ["@b.ru"])


@pytest.mark.parametrize("header", [b"boss@firma.example, spam@other.example", b"not an address"])
def test_sender_ambiguous_is_not_trusted(header):
    assert parse_letter(b"From: " + header + b"\r\n\r\nbody").sender == ""


def test_two_from_headers_are_not_trusted():
    raw = b"From: boss@firma.example\r\nFrom: spam@other.example\r\n\r\nbody"
    assert parse_letter(raw).sender == ""


def test_display_name_does_not_spoof_sender():
    raw = b'From: "boss@firma.example" <Spam@other.example>\r\n\r\nbody'
    assert parse_letter(raw).sender == "spam@other.example"


async def test_empty_allowed_means_all(env):
    env.mail.add(letter(sender="anyone@anywhere.example", pdfs=(pdf_bytes(1),)))
    await env.poll()
    assert len(await env.docs()) == 1


async def test_edit_of_filter_rechecks_skipped_letters(env):
    await env.set_box(allowed_senders=["@firma.example"])
    env.mail.add(letter(sender="boss@other.example", pdfs=(pdf_bytes(1),)))
    await env.poll()
    assert await env.docs() == []
    await env.set_box(allowed_senders=["@firma.example", "boss@other.example"])
    await env.poll()
    assert len(await env.docs()) == 1


# --- идемпотентность ----------------------------------------------------


async def test_replay_same_message_no_second_document(env):
    env.mail.add(letter(pdfs=(pdf_bytes(1),), images=(png_bytes(),)))
    env.mail.fail[("mark_seen", "1")] = MailboxNetworkError("TimeoutError")
    await env.poll()
    assert len(await env.docs()) == 2
    assert (await env.box()).last_error == NETWORK_ERROR
    calls = len(env.enqueuer.calls)
    del env.mail.fail[("mark_seen", "1")]
    await env.poll()
    assert len(await env.docs()) == 2
    assert len(env.enqueuer.calls) == calls
    async with env.sm() as s:
        assert await s.scalar(select(func.count()).select_from(Export)) == 0
    assert env.mail.seen == {"1"}
    assert (await env.box()).last_error is None


async def test_no_message_id_uses_content_hash(env):
    same = letter(pdfs=(pdf_bytes(1),), message_id=None)
    env.mail.add(same)
    env.mail.add(same)
    env.mail.add(letter(subject="Другое", pdfs=(pdf_bytes(1),), message_id=None))
    await env.poll()
    docs = await env.docs()
    assert len(docs) == 2
    assert all(d.source_ref["message_id"] is None for d in docs)
    assert env.mail.seen == {"1", "2", "3"}


async def test_idempotency_key_format(env):
    env.mail.add(letter(pdfs=(pdf_bytes(1),), images=(png_bytes(),)))
    await env.poll()
    docs = await env.by_attachment()
    digest = hashlib.sha256(b"<m1@firma.example>").hexdigest()
    assert docs["pdf:0"].idempotency_key == f"mail:{env.box_id}:{digest}:pdf:0"
    assert docs["images"].idempotency_key == f"mail:{env.box_id}:{digest}:images"


# --- исходы приёма -----------------------------------------------------


async def exhaust_limit(env):
    async with env.sm() as s:
        tenant = await s.get(Tenant, env.tenant_id)
        tenant.pages_limit = 50
        used = {"schema_slug": "invoice_ru", "schema_version": 1, "files": [], "mode": "gray", "pages": 50}
        s.add(Document(tenant_id=env.tenant_id, **used))
        await s.commit()


async def test_limit_marks_seen_and_shows_error(env):
    await exhaust_limit(env)
    env.mail.add(letter(pdfs=(pdf_bytes(1),)))
    await env.poll()
    assert len(await env.docs()) == 1  # только документ из подготовки
    assert env.mail.seen == {"1"}
    assert (await env.box()).last_error == "Лимит страниц на месяц исчерпан (50 из 50)"


async def test_permanent_error_marks_seen_and_names_letter(env):
    env.mail.add(letter(subject="Счёт\r\n апрель", pdfs=(b"%PDF-broken",)))
    await env.poll()
    assert await env.docs() == []
    assert env.mail.seen == {"1"}
    error = (await env.box()).last_error
    assert error.startswith("Письмо «Счёт апрель»: ") and "\n" not in error


async def test_broken_pdf_does_not_lose_other_attachment(env):
    env.mail.add(letter(pdfs=(b"%PDF-broken", pdf_bytes(1))))
    await env.poll()
    assert set(await env.by_attachment()) == {"pdf:1"}
    assert env.mail.seen == {"1"}
    assert (await env.box()).last_error.startswith("Письмо «Документы»: ")


async def test_error_of_earlier_letter_survives_later_success_in_pass(env):
    env.mail.add(letter(subject="Битый", pdfs=(b"%PDF-broken",), message_id="<a@x>"))
    env.mail.add(letter(subject="Хороший", pdfs=(pdf_bytes(1),), message_id="<b@x>"))
    await env.poll()
    assert (await env.box()).last_error.startswith("Письмо «Битый»: ")
    # следующий проход с успешным письмом ошибку снимает
    env.mail.add(letter(subject="Ещё", pdfs=(pdf_bytes(1),), message_id="<c@x>"))
    await env.poll()
    assert (await env.box()).last_error is None


async def test_storage_failure_not_marked_seen(env):
    env.mail.add(letter(pdfs=(pdf_bytes(1),)))
    env.blobs.broken = True
    await env.poll()
    assert env.mail.called("mark_seen") == []
    assert await env.docs() == []
    assert "Не удалось сохранить файлы" in (await env.box()).last_error
    env.blobs.broken = False
    await env.poll()
    assert len(await env.docs()) == 1
    assert env.mail.seen == {"1"}
    assert (await env.box()).last_error is None


async def test_enqueue_failure_not_marked_seen_and_retried(env):
    env.mail.add(letter(pdfs=(pdf_bytes(1),)))
    original = env.enqueuer.enqueue

    async def broken(*args, **kwargs):
        raise ConnectionError("redis")

    env.enqueuer.enqueue = broken
    await env.poll()
    assert env.mail.called("mark_seen") == []
    env.enqueuer.enqueue = original
    await env.poll()
    assert env.mail.seen == {"1"}
    assert sorted(d.status for d in await env.docs()) == ["failed", "queued"]


async def test_unexpected_accept_failure_is_retried_without_text(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    env.mail.add(letter(pdfs=(pdf_bytes(1),)))
    real = mailbox_module.accept_document

    async def boom(*args, **kwargs):
        raise RuntimeError(PASSWORD)

    monkeypatch.setattr(mailbox_module, "accept_document", boom)
    await env.poll()
    assert env.mail.called("mark_seen") == []
    box = await env.box()
    assert box.last_error.startswith("Письмо «Документы»: ") and PASSWORD not in box.last_error
    assert PASSWORD not in caplog.text
    monkeypatch.setattr(mailbox_module, "accept_document", real)
    await env.poll()
    assert env.mail.seen == {"1"} and len(await env.docs()) == 1


async def test_oversized_letter_not_fetched(env):
    limit = env.settings.max_request_mb * 1024 * 1024
    env.mail.add(letter(subject="Огромное письмо"), size=limit + 1)
    await env.poll()
    # тему отдаёт отдельный запрос одного заголовка: тело письма не качается
    assert env.mail.calls == [("size", "1"), ("subject", "1"), ("mark_seen", "1")]
    assert (await env.box()).last_error == f"Письмо «Огромное письмо» больше {env.settings.max_request_mb} МБ"


async def test_letter_exactly_at_limit_is_fetched(env):
    env.mail.add(letter(pdfs=(pdf_bytes(1),)), size=env.settings.max_request_mb * 1024 * 1024)
    await env.poll()
    assert env.mail.called("fetch") == ["1"]


async def test_last_error_cut_to_500(env):
    env.mail.add(letter(subject="Т" * 900, pdfs=(b"%PDF-broken",)))
    await env.poll()
    assert len((await env.box()).last_error) == 500


async def test_pass_cap(env):
    assert MAX_LETTERS_PER_PASS == 20
    content = pdf_bytes(1)
    for n in range(25):
        env.mail.add(letter(pdfs=(content,), message_id=f"<{n}@x>"))
    await env.poll()
    assert len(env.mail.seen) == 20 and len(await env.docs()) == 20
    await env.poll()
    assert len(env.mail.seen) == 25 and len(await env.docs()) == 25


async def test_skipped_letters_do_not_use_up_cap(env):
    for n in range(25):
        env.mail.add(letter(message_id=f"<{n}@x>"))
    env.mail.add(letter(pdfs=(pdf_bytes(1),), message_id="<last@x>"))
    await env.poll()
    assert await env.docs() == []
    await env.poll()
    assert len(await env.docs()) == 1


# --- подключение ---------------------------------------------------------


async def test_auth_error_stops_until_edit(env, caplog):
    caplog.set_level(logging.DEBUG)

    class RejectingImap:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, user, password):
            raise imaplib.IMAP4.error(f"LOGIN {user} {password} failed")

        def logout(self):
            pass

    env.poller = MailboxPoller(env.deps, connect=lambda creds: open_imap(creds, factory=RejectingImap))
    await env.poll()
    box = await env.box()
    assert box.active is False
    assert box.last_error == AUTH_ERROR == "Почта не приняла логин или пароль"
    assert PASSWORD not in box.last_error and PASSWORD not in caplog.text

    env.poller = MailboxPoller(env.deps, connect=env.connect)
    await env.poll()
    assert env.connects == 0  # ящик стоит до правки в кабинете
    await env.set_box(active=True, last_error=None)
    await env.poll()
    assert env.connects == 1


async def test_auth_failure_does_not_stop_mailbox_edited_during_pass(env):
    new_password = encrypt_secrets({"password": "synthetic-new-pass"}, KEY)
    box_id = env.box_id

    def connect(creds):
        raise MailboxAuthError(AUTH_ERROR)

    class Editing(MailboxPoller):
        async def _load(self, mailbox_id):
            loaded = await super()._load(mailbox_id)
            # правка приходит уже после того, как проход взял снимок настроек
            async with env.sm() as s:
                await s.execute(update(Mailbox).where(Mailbox.id == box_id).values(password_enc=new_password))
                await s.commit()
            return loaded

    env.poller = Editing(env.deps, connect=connect)
    await env.poll()
    assert (await env.box()).active is True


async def test_network_error_keeps_active_and_clears_on_recovery(env):
    env.connect_error = MailboxNetworkError("TimeoutError")
    await env.poll()
    box = await env.box()
    assert box.active is True and box.last_error == NETWORK_ERROR == "Не удалось подключиться к почте"
    env.connect_error = None
    await env.poll()
    assert (await env.box()).last_error is None


async def test_login_clears_only_connection_errors(env):
    letter_error = "Письмо «Счёт» больше 200 МБ"
    await env.set_box(last_error=letter_error)
    await env.poll()
    assert (await env.box()).last_error == letter_error


async def test_host_error_clears_after_login(env):
    env.settings.webhook_allow_private = False
    await env.set_box(host="10.0.0.1")
    await env.poll()
    assert (await env.box()).last_error
    env.settings.webhook_allow_private = True
    await env.poll()
    assert (await env.box()).last_error is None


async def test_unknown_connect_failure_is_network_and_has_no_text(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.connect_error = RuntimeError(f"LOGIN {PASSWORD}")
    await env.poll()
    assert (await env.box()).last_error == NETWORK_ERROR
    assert PASSWORD not in caplog.text


async def test_unseen_failure_is_network_and_closes(env):
    def broken():
        raise MailboxNetworkError("abort")

    env.mail.unseen_uids = broken
    await env.poll()
    assert (await env.box()).last_error == NETWORK_ERROR
    assert env.mail.closed == 1


async def test_private_host_refused_at_poll(env):
    env.settings.webhook_allow_private = False
    await env.set_box(host="10.0.0.1")
    await env.poll()
    assert env.connects == 0
    box = await env.box()
    assert box.last_error and box.active is True
    assert "10.0.0.1" not in box.last_error


async def test_unresolvable_host_is_network_error(env, monkeypatch):
    env.settings.webhook_allow_private = False

    def no_dns(*args, **kwargs):
        raise OSError("dns down")

    monkeypatch.setattr("socket.getaddrinfo", no_dns)
    await env.poll()
    assert env.connects == 0
    assert (await env.box()).last_error == NETWORK_ERROR


def test_check_host():
    check_host("10.0.0.1", 993, allow_private=True)
    check_host("8.8.8.8", 993, allow_private=False)
    with pytest.raises(ValueError):
        check_host("10.0.0.1", 993, allow_private=False)
    with pytest.raises(ValueError):
        check_host("127.0.0.1", 993, allow_private=False)


async def test_secrets_key_failure_keeps_active(env):
    env.settings.secrets_key = ""
    await env.poll()
    box = await env.box()
    assert env.connects == 0
    assert box.active is True and "DOCFLOW_SECRETS_KEY" in box.last_error


async def test_inactive_or_deleted_mailbox_not_polled(env):
    await env.set_box(active=False)
    await env.poll()
    assert env.connects == 0
    async with env.sm() as s:
        await s.delete(await s.get(Mailbox, env.box_id))
        await s.commit()
    await env.poll()
    assert env.connects == 0


async def test_source_ref_has_no_personal_data(env):
    raw = letter(subject="Иванов И.И.", sender="ivanov@firma.example", pdfs=(pdf_bytes(1),), images=(png_bytes(),))
    env.mail.add(raw)
    await env.poll()
    docs = await env.by_attachment()
    assert docs["pdf:0"].source_ref == {"message_id": "<m1@firma.example>", "attachment": "pdf:0"}
    assert docs["images"].source_ref == {"message_id": "<m1@firma.example>", "attachment": "images"}


async def test_broken_letter_does_not_stop_pass(env, monkeypatch):
    env.mail.add(letter(message_id="<a@x>", pdfs=(pdf_bytes(1),)))
    env.mail.add(b"\x00\xff not a letter at all")
    env.mail.add(letter(message_id="<c@x>", pdfs=(pdf_bytes(1),)))
    env.mail.add(letter(message_id="<d@x>", pdfs=(pdf_bytes(1),)))
    env.mail.fail[("fetch", "1")] = MailboxLetterError("NO")
    real = parse_letter

    def picky(raw):
        if b"<c@x>" in raw:
            raise IndexError("сломанный заголовок")
        return real(raw)

    monkeypatch.setattr(mailbox_module, "parse_letter", picky)
    await env.poll()
    assert [d.source_ref["message_id"] for d in await env.docs()] == ["<d@x>"]
    assert env.mail.seen == {"4"}
    assert (await env.box()).active is True
    await env.poll()
    assert env.mail.called("fetch").count("3") == 1  # сломанное письмо не качаем каждую минуту


async def test_dead_connection_stops_pass(env):
    env.mail.add(letter(message_id="<a@x>", pdfs=(pdf_bytes(1),)))
    env.mail.add(letter(message_id="<b@x>", pdfs=(pdf_bytes(1),)))
    env.mail.fail[("size", "1")] = MailboxNetworkError("abort")
    await env.poll()
    assert env.mail.calls == [("size", "1")]
    assert (await env.box()).last_error == NETWORK_ERROR


def test_parse_letter_survives_bad_bytes():
    raw = (
        b"From: =?utf-8?B?/w==?= <a@b.ru>\r\n"
        b"Subject: \xff\xfe\x00 \xd0\xa1\xd1\x87\r\n"
        b"Message-ID: <\x00x@y>\r\n\r\nbody"
    )
    parsed = parse_letter(raw)
    for text in (parsed.subject, parsed.message_id):
        assert "\x00" not in text
        text.encode("utf-8")  # в Postgres суррогаты и NUL не записать


def test_attachment_filename_is_clean():
    msg = EmailMessage()
    msg["From"] = SENDER
    msg.add_attachment(pdf_bytes(1), maintype="application", subtype="pdf", filename="счёт\x07.pdf")
    msg.add_attachment(png_bytes(), maintype="image", subtype="png")
    parsed = parse_letter(msg.as_bytes())
    assert parsed.pdfs[0].name == "счёт.pdf"
    assert parsed.images[0].name


def test_attachment_kind_is_by_content_not_header():
    msg = EmailMessage()
    msg["From"] = SENDER
    msg.add_attachment(pdf_bytes(1), maintype="application", subtype="octet-stream", filename="x.bin")
    msg.add_attachment(png_bytes(), maintype="application", subtype="octet-stream", filename="y.bin")
    msg.add_attachment(b"MZ exe", maintype="application", subtype="pdf", filename="z.pdf")
    parsed = parse_letter(msg.as_bytes())
    assert [a.name for a in parsed.pdfs] == ["x.bin"] and [a.name for a in parsed.images] == ["y.bin"]


# --- open_imap -------------------------------------------------------------


CREDS = MailboxCreds("imap.example.test", 993, "in@acme.example", PASSWORD, "Inbox Bills")


class StubImap:
    """Заглушка imaplib: отвечает в тех же структурах, что IMAP4.uid после разбора ответа сервера."""

    last = None
    uid_error: Exception | None = None

    def __init__(self, host, port, *, ssl_context=None, timeout=None):
        self.host, self.port, self.ssl_context, self.timeout = host, port, ssl_context, timeout
        self.commands: list[tuple] = []
        self.logged_in = None
        self.selected = None
        self.logged_out = False
        type(self).last = self

    def login(self, user, password):
        password.encode("ascii")  # imaplib кодирует команду в ASCII
        self.logged_in = (user, password)
        return "OK", [b"logged in"]

    def select(self, mailbox="INBOX", readonly=False):
        self.selected = (mailbox, readonly)
        return "OK", [b"3"]

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if type(self).uid_error is not None:
            raise type(self).uid_error
        if command == "SEARCH":
            return "OK", [b"7 12 30"]
        if command == "STORE":
            return "OK", [b"7 (UID 7 FLAGS (\\Seen))"]
        if "RFC822.SIZE" in args[-1]:
            return "OK", [b"5 (UID 7 RFC822.SIZE 4242)"]
        if "HEADER.FIELDS" in args[-1]:
            return "OK", [(b"5 (UID 7 BODY[HEADER.FIELDS (SUBJECT)] {30}", "Subject: Тест\r\n\r\n".encode()), b")"]
        return "OK", [(b"5 (UID 7 BODY[] {5}", b"HELLO"), b")"]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"bye"]


@pytest.fixture
def stub(monkeypatch):
    class Fresh(StubImap):
        pass

    return Fresh


def test_imap_verifies_certificate(stub):
    session = open_imap(CREDS, factory=stub)
    conn = stub.last
    assert isinstance(conn.ssl_context, ssl.SSLContext)
    assert conn.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert conn.ssl_context.check_hostname is True
    assert conn.timeout == 20
    assert (conn.host, conn.port) == ("imap.example.test", 993)
    assert conn.logged_in == ("in@acme.example", PASSWORD)
    assert conn.selected == ('"Inbox Bills"', False)
    session.close()
    assert conn.logged_out is True


def test_imap_session_uses_peek_and_uid(stub):
    session = open_imap(CREDS, factory=stub)
    assert session.unseen_uids() == ["7", "12", "30"]
    assert session.size("7") == 4242
    assert session.subject("7") == "Тест"
    assert session.fetch("7") == b"HELLO"
    session.mark_seen("7")
    assert stub.last.commands == [
        ("SEARCH", None, "UNSEEN"),
        ("FETCH", "7", "(RFC822.SIZE)"),
        ("FETCH", "7", "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])"),
        ("FETCH", "7", "(BODY.PEEK[])"),
        ("STORE", "7", "+FLAGS", "(\\Seen)"),
    ]


@pytest.mark.parametrize("empty", [[b""], [None]])
def test_imap_empty_search(stub, monkeypatch, empty):
    session = open_imap(CREDS, factory=stub)
    monkeypatch.setattr(stub.last, "uid", lambda *a: ("OK", empty))
    assert session.unseen_uids() == []


def test_imap_letter_gone_is_letter_error(stub, monkeypatch):
    session = open_imap(CREDS, factory=stub)
    monkeypatch.setattr(stub.last, "uid", lambda *a: ("OK", [None]))
    for call in (session.size, session.fetch, session.subject):
        with pytest.raises(MailboxLetterError):
            call("7")
    monkeypatch.setattr(stub.last, "uid", lambda *a: ("NO", [b"nope"]))
    with pytest.raises(MailboxLetterError):
        session.mark_seen("7")


@pytest.mark.parametrize(
    "error",
    [imaplib.IMAP4.abort(f"LOGIN {PASSWORD}"), OSError(PASSWORD), EOFError(PASSWORD), TimeoutError(PASSWORD)],
)
def test_imap_broken_connection_is_network_error_without_text(stub, error):
    session = open_imap(CREDS, factory=stub)
    stub.uid_error = error
    with pytest.raises(MailboxNetworkError) as info:
        session.fetch("7")
    assert PASSWORD not in str(info.value) and info.value.__suppress_context__


def test_imap_command_error_is_letter_error_without_text(stub):
    session = open_imap(CREDS, factory=stub)
    stub.uid_error = imaplib.IMAP4.error(f"command error {PASSWORD}")
    with pytest.raises(MailboxLetterError) as info:
        session.fetch("7")
    assert PASSWORD not in str(info.value)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (imaplib.IMAP4.error(f"LOGIN {PASSWORD} rejected"), MailboxAuthError),
        (imaplib.IMAP4.abort(f"LOGIN {PASSWORD} dropped"), MailboxNetworkError),
        (OSError(PASSWORD), MailboxNetworkError),
    ],
)
def test_imap_login_failures(stub, monkeypatch, error, expected):
    def fail(self, user, password):
        raise error

    monkeypatch.setattr(stub, "login", fail)
    with pytest.raises(expected) as info:
        open_imap(CREDS, factory=stub)
    assert PASSWORD not in str(info.value) and info.value.__suppress_context__
    assert stub.last.logged_out is True


def test_imap_non_ascii_password_is_auth_error(stub):
    creds = MailboxCreds("imap.example.test", 993, "in@acme.example", "пароль-синтетика", "INBOX")
    with pytest.raises(MailboxAuthError) as info:
        open_imap(creds, factory=stub)
    assert "пароль-синтетика" not in str(info.value)


def test_imap_connect_failure_is_network_error():
    def refuse(*args, **kwargs):
        raise ssl.SSLCertVerificationError(f"bad cert {PASSWORD}")

    with pytest.raises(MailboxNetworkError) as info:
        open_imap(CREDS, factory=refuse)
    assert PASSWORD not in str(info.value)


def test_imap_bad_folder_is_network_error_and_closes(stub, monkeypatch):
    monkeypatch.setattr(stub, "select", lambda self, mailbox, readonly=False: ("NO", [b"no such folder"]))
    with pytest.raises(MailboxNetworkError):
        open_imap(CREDS, factory=stub)
    assert stub.last.logged_out is True


def test_imap_non_ascii_folder_is_network_error(stub, monkeypatch):
    def select(self, mailbox, readonly=False):
        mailbox.encode("ascii")

    monkeypatch.setattr(stub, "select", select)
    creds = MailboxCreds("imap.example.test", 993, "in@acme.example", PASSWORD, "Счета")
    with pytest.raises(MailboxNetworkError):
        open_imap(creds, factory=stub)


def test_creds_repr_has_no_password():
    assert PASSWORD not in repr(CREDS)
