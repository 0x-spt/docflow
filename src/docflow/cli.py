"""Командная строка оператора: эталонный прогон, создание клиента, загрузка встроенных схем.

    docflow eval evals/invoice_ru --extractor claude
    docflow create-tenant "Ромашка" --extractor claude
    docflow seed-schemas
    docflow seed-demo --reset
"""
import argparse
import asyncio
import json
import logging
import secrets
import string
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from docflow.auth import MIN_PASSWORD, hash_password
from docflow.config import get_settings
from docflow.db.models import ApiKey, Lead, SchemaRecord, Tenant, User, generate_api_key
from docflow.db.session import make_engine, make_sessionmaker
from docflow.evaluation import format_report, run_eval
from docflow.extraction.base import Extractor
from docflow.schemas.library import builtin_schemas
from docflow.targets import targets_text


class UserError(Exception):
    """Ошибка пользователя: печатаем сообщение без трейсбека, код возврата 1."""


def _make_extractor(kind: str, fake_responses: Path | None, slug: str) -> Extractor:
    settings = get_settings()
    if kind == "fake":
        path = fake_responses or settings.fake_responses
        if not path.is_file():
            raise UserError(f"файл с заготовленными ответами не найден: {path} (укажите --fake-responses)")
        from docflow.extraction.fake import FakeExtractor

        try:
            responses = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise UserError(f"{path}: некорректный JSON ({e})") from e
        if not isinstance(responses, dict) or slug not in responses:
            raise UserError(f"в {path} нет ответа для схемы «{slug}»")
        return FakeExtractor(responses)
    if kind == "ollama":
        from docflow.extraction.ollama import OllamaExtractor

        return OllamaExtractor(settings.ollama_url, settings.ollama_model)
    from docflow.extraction.claude import ClaudeExtractor

    return ClaudeExtractor(model=settings.anthropic_model)


def cmd_eval(args: argparse.Namespace) -> int:
    root: Path = args.dir
    if not root.is_dir():
        raise UserError(f"папка набора не найдена: {root}")
    extractor = _make_extractor(args.extractor, args.fake_responses, root.name)
    try:
        report = run_eval(root, extractor, mode=args.mode)
    except ValueError as e:
        raise UserError(str(e)) from e
    print(format_report(report))
    out = args.out or root.parent / "eval-report.json"
    out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт записан: {out}")
    return 0


async def _create_tenant(name: str, extractor: str, auto_ok: bool) -> tuple[uuid.UUID, str]:
    engine = make_engine(get_settings().database_url)
    try:
        plain, prefix, key_hash = generate_api_key()
        async with make_sessionmaker(engine)() as session:
            tenant = Tenant(name=name, extractor=extractor, auto_ok=auto_ok)
            session.add(tenant)
            await session.flush()
            session.add(ApiKey(tenant_id=tenant.id, prefix=prefix, key_hash=key_hash))
            await session.commit()
        return tenant.id, plain
    finally:
        await engine.dispose()


def _auto_ok_line(auto_ok: bool) -> str:
    if auto_ok:
        return "Статус ok ставится автоматически, если все проверки пройдены."
    return (
        "Результаты — черновик: всё уходит в needs_review, пока эталонный прогон не подтвердит точность модели "
        "(потом: docflow set-auto-ok <id> yes)."
    )


def cmd_create_tenant(args: argparse.Namespace) -> int:
    name = args.name.strip()
    if not name:
        raise UserError("имя клиента не может быть пустым")
    # локальная модель уверенно ошибается в фамилиях и названиях, пока её точность не измерена
    auto_ok = args.extractor == "claude" if args.auto_ok is None else args.auto_ok == "yes"
    tenant_id, key = asyncio.run(_create_tenant(name, args.extractor, auto_ok))
    print(f"Клиент «{name}» создан, id {tenant_id}.")
    print(_auto_ok_line(auto_ok))
    print(f"API-ключ: {key}")
    print("ВНИМАНИЕ: ключ больше не будет показан. Сохраните его сейчас.")
    return 0


async def _set_auto_ok(tenant_id: uuid.UUID, auto_ok: bool) -> bool:
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                return False
            tenant.auto_ok = auto_ok
            await session.commit()
            return True
    finally:
        await engine.dispose()


def cmd_set_auto_ok(args: argparse.Namespace) -> int:
    try:
        tenant_id = uuid.UUID(args.tenant_id)
    except ValueError:
        raise UserError(f"«{args.tenant_id}» — не id клиента") from None
    auto_ok = args.value == "yes"
    if not asyncio.run(_set_auto_ok(tenant_id, auto_ok)):
        raise UserError(f"клиент {tenant_id} не найден")
    print(_auto_ok_line(auto_ok))
    return 0


async def _create_user(tenant_id: uuid.UUID, email: str, role: str, password: str) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            if await session.get(Tenant, tenant_id) is None:
                raise UserError(f"клиент {tenant_id} не найден")
            if (await session.execute(select(User.id).where(User.email == email))).first() is not None:
                raise UserError(f"пользователь {email} уже есть")
            session.add(User(tenant_id=tenant_id, email=email, password_hash=hash_password(password), role=role))
            try:
                await session.commit()
            except IntegrityError:  # гонка двух одинаковых create-user
                raise UserError(f"пользователь {email} уже есть") from None
    finally:
        await engine.dispose()


def cmd_create_user(args: argparse.Namespace) -> int:
    try:
        tenant_id = uuid.UUID(args.tenant_id)
    except ValueError:
        raise UserError(f"«{args.tenant_id}» — не id клиента") from None
    email = args.email.strip().lower()
    if "@" not in email:
        raise UserError(f"«{args.email}» — не email")
    generated = args.password is None
    password = args.password or "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    if len(password) < MIN_PASSWORD:
        raise UserError(f"пароль короче {MIN_PASSWORD} символов")
    asyncio.run(_create_user(tenant_id, email, args.role, password))
    print(f"Пользователь {email} создан, роль {args.role}.")
    if generated:
        print(f"Пароль: {password}")
        print("ВНИМАНИЕ: пароль больше не будет показан. Передайте его сейчас.")
    return 0


async def _seed_schemas() -> tuple[int, int]:
    """Добавляет недостающие встроенные схемы и обновляет body у существующих (tenant_id NULL), если он изменился."""
    engine = make_engine(get_settings().database_url)
    added = updated = 0
    try:
        async with make_sessionmaker(engine)() as session:
            rows = (await session.execute(select(SchemaRecord).where(SchemaRecord.tenant_id.is_(None)))).scalars()
            existing = {(r.slug, r.version): r for r in rows}
            for slug, schema in builtin_schemas().items():
                body = schema.model_dump(mode="json")
                row = existing.get((slug, schema.version))
                if row is None:
                    session.add(SchemaRecord(tenant_id=None, slug=slug, version=schema.version, body=body))
                    added += 1
                elif row.body != body:
                    row.body = body
                    updated += 1
            await session.commit()
    finally:
        await engine.dispose()
    return added, updated


def cmd_seed_schemas(args: argparse.Namespace) -> int:
    added, updated = asyncio.run(_seed_schemas())
    print(f"Встроенных схем добавлено: {added}, обновлено: {updated}")
    return 0


async def _recent_leads(limit: int) -> list[Lead]:
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            q = select(Lead).order_by(Lead.created_at.desc()).limit(limit)
            return list((await session.execute(q)).scalars())
    finally:
        await engine.dispose()


def cmd_leads(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise UserError("--limit должен быть больше нуля")
    leads = asyncio.run(_recent_leads(args.limit))
    if not leads:
        print("Заявок пока нет.")
        return 0
    rows = [("Дата", "Имя", "Компания", "Контакт", "Объём", "Куда выгружать")]
    rows += [
        (
            lead.created_at.strftime("%Y-%m-%d %H:%M"),
            lead.name,
            lead.company or "-",
            lead.contact,
            lead.volume,
            targets_text(lead.targets) or "-",
        )
        for lead in leads
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(6)]
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())
    return 0


def _read_google_key(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise UserError(f"ключ Google не прочитан: {path} ({e.strerror or e})") from e
    except ValueError as e:
        raise UserError(f"{path}: некорректный JSON ({e})") from e
    if not isinstance(key, dict):
        raise UserError(f"{path}: ожидается JSON-объект ключа сервисного аккаунта")
    return key


async def _seed_demo(opts):
    # демо-модули тянут Pillow/OpenCV и шрифты: грузим только для этой команды
    from arq import create_pool
    from arq.connections import RedisSettings

    from docflow.demo.seed import seed_demo
    from docflow.pdf.build import OcrMyPdf
    from docflow.queue import ArqEnqueuer
    from docflow.storage.blobs import blob_store_from_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        return await seed_demo(
            make_sessionmaker(engine), blob_store_from_settings(settings), OcrMyPdf(), ArqEnqueuer(pool), settings, opts
        )
    finally:
        await pool.aclose()
        await engine.dispose()


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from docflow.demo.data import DEMO_TENANT_NAME
    from docflow.demo.render import FontMissing
    from docflow.demo.seed import DemoError, DemoExists, SeedOptions

    opts = SeedOptions(
        reset=args.reset,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        google_key=_read_google_key(args.google_key),
        google_sheet=args.google_sheet,
        bitrix_webhook=args.bitrix_webhook,
        telegram_token=args.telegram_token,
        telegram_chat=args.telegram_chat,
    )
    try:
        result = asyncio.run(_seed_demo(opts))
    except DemoExists:
        raise UserError(f"демо-клиент «{DEMO_TENANT_NAME}» уже есть; пересоздать: docflow seed-demo --reset") from None
    except (DemoError, FontMissing) as e:
        raise UserError(str(e)) from e
    settings = get_settings()
    print(f"Демо-клиент «{DEMO_TENANT_NAME}» создан, id {result.tenant_id}.")
    print(f"Кабинет: {settings.public_site_url.rstrip('/')}/login")
    print(f"Владелец: {result.owner_email} / {result.owner_password}")
    print(f"Оператор: {result.operator_email} / {result.operator_password}")
    print("ВНИМАНИЕ: пароль больше не будет показан. Сохраните его сейчас.")
    print(f"API-ключ: {result.api_key}")
    print("Документы: " + ", ".join(f"{status} — {n}" for status, n in sorted(result.counts.items())))
    print("Это демо: данные синтетические, модель — заготовки (model=demo)")
    if args.smtp_host == "mailpit":
        print("Письма: http://localhost:8025 (Mailpit)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docflow", description="DocFlow: команды оператора")
    sub = parser.add_subparsers(dest="command", required=True, metavar="команда")

    ev = sub.add_parser("eval", help="эталонный прогон: измерить качество извлечения на папке документов")
    ev.add_argument("dir", type=Path, help="папка набора evals/<slug>; имя папки — slug встроенной схемы")
    ev.add_argument("--extractor", choices=["claude", "ollama", "fake"], default="claude", help="чем извлекать")
    ev.add_argument("--fake-responses", type=Path, help="JSON {slug: ответ} для --extractor fake")
    ev.add_argument("--mode", default="gray", choices=["gray", "color", "bw"], help="режим обработки страниц")
    ev.add_argument("--out", type=Path, help="куда записать eval-report.json (по умолчанию рядом с набором)")
    ev.set_defaults(func=cmd_eval)

    ct = sub.add_parser("create-tenant", help="создать клиента и выдать API-ключ")
    ct.add_argument("name", help="название клиента")
    ct.add_argument("--extractor", choices=["claude", "ollama"], default="claude", help="чем извлекать для клиента")
    ct.add_argument(
        "--auto-ok",
        choices=["yes", "no"],
        help="ставить ok без человека (по умолчанию: да для claude, нет для ollama)",
    )
    ct.set_defaults(func=cmd_create_tenant)

    sa = sub.add_parser("set-auto-ok", help="разрешить или запретить клиенту автоматический ok")
    sa.add_argument("tenant_id", help="id клиента (печатается при create-tenant)")
    sa.add_argument("value", choices=["yes", "no"])
    sa.set_defaults(func=cmd_set_auto_ok)

    cu = sub.add_parser("create-user", help="создать пользователя кабинета для клиента")
    cu.add_argument("tenant_id", help="id клиента (печатается при create-tenant)")
    cu.add_argument("email")
    cu.add_argument("--role", choices=["owner", "operator"], default="owner")
    cu.add_argument("--password", help=f"пароль (от {MIN_PASSWORD} символов); без него будет сгенерирован")
    cu.set_defaults(func=cmd_create_user)

    ld = sub.add_parser("leads", help="последние заявки на аудит с сайта")
    ld.add_argument("--limit", type=int, default=20, help="сколько заявок показать (по умолчанию 20)")
    ld.set_defaults(func=cmd_leads)

    ss = sub.add_parser("seed-schemas", help="загрузить встроенные схемы в БД (повторный запуск безопасен)")
    ss.set_defaults(func=cmd_seed_schemas)

    sd = sub.add_parser("seed-demo", help="создать демо-клиента с документами (только для показа)")
    sd.add_argument("--reset", action="store_true", help="удалить прежнего демо-клиента и создать заново")
    sd.add_argument("--smtp-host", default="mailpit", help="SMTP для почтовых подключений (по умолчанию mailpit)")
    sd.add_argument("--smtp-port", type=int, default=1025, help="порт SMTP (по умолчанию 1025)")
    sd.add_argument("--google-key", type=Path, help="JSON-ключ сервисного аккаунта Google (вместе с --google-sheet)")
    sd.add_argument("--google-sheet", help="ID или ссылка Google Таблицы")
    sd.add_argument("--bitrix-webhook", help="входящий вебхук Битрикс24")
    sd.add_argument("--telegram-token", help="токен бота Telegram (вместе с --telegram-chat)")
    sd.add_argument("--telegram-chat", help="ID чата Telegram")
    sd.set_defaults(func=cmd_seed_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UserError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
    except (SQLAlchemyError, OSError) as e:
        print(f"Ошибка: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}", file=sys.stderr)
    except Exception as e:  # оператору — сообщение, а не трейсбек
        print(f"Ошибка: {type(e).__name__}: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
