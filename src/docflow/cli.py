"""Командная строка оператора: эталонный прогон, создание клиента, загрузка встроенных схем.

    docflow eval evals/invoice_ru --extractor claude
    docflow create-tenant "Ромашка" --extractor claude
    docflow seed-schemas
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from docflow.config import get_settings
from docflow.db.models import ApiKey, SchemaRecord, Tenant, generate_api_key
from docflow.db.session import make_engine, make_sessionmaker
from docflow.evaluation import format_report, run_eval
from docflow.extraction.base import Extractor
from docflow.schemas.library import builtin_schemas


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


async def _create_tenant(name: str, extractor: str) -> str:
    engine = make_engine(get_settings().database_url)
    try:
        plain, prefix, key_hash = generate_api_key()
        async with make_sessionmaker(engine)() as session:
            tenant = Tenant(name=name, extractor=extractor)
            session.add(tenant)
            await session.flush()
            session.add(ApiKey(tenant_id=tenant.id, prefix=prefix, key_hash=key_hash))
            await session.commit()
        return plain
    finally:
        await engine.dispose()


def cmd_create_tenant(args: argparse.Namespace) -> int:
    name = args.name.strip()
    if not name:
        raise UserError("имя клиента не может быть пустым")
    key = asyncio.run(_create_tenant(name, args.extractor))
    print(f"Клиент «{name}» создан.")
    print(f"API-ключ: {key}")
    print("ВНИМАНИЕ: ключ больше не будет показан. Сохраните его сейчас.")
    return 0


async def _seed_schemas() -> int:
    engine = make_engine(get_settings().database_url)
    added = 0
    try:
        async with make_sessionmaker(engine)() as session:
            q = select(SchemaRecord.slug, SchemaRecord.version).where(SchemaRecord.tenant_id.is_(None))
            existing = set((await session.execute(q)).all())
            for slug, schema in builtin_schemas().items():
                if (slug, schema.version) in existing:
                    continue
                session.add(
                    SchemaRecord(tenant_id=None, slug=slug, version=schema.version, body=schema.model_dump(mode="json"))
                )
                added += 1
            await session.commit()
    finally:
        await engine.dispose()
    return added


def cmd_seed_schemas(args: argparse.Namespace) -> int:
    print(f"Встроенных схем добавлено: {asyncio.run(_seed_schemas())}")
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
    ct.set_defaults(func=cmd_create_tenant)

    ss = sub.add_parser("seed-schemas", help="загрузить встроенные схемы в БД (повторный запуск безопасен)")
    ss.set_defaults(func=cmd_seed_schemas)
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
