"""Каталог типов документов, видимых тенанту: встроенные и свои (последняя версия каждого slug)."""
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import SchemaRecord
from docflow.schemas.library import builtin_schemas

# служебный slug: «определить тип автоматически»; в каталоге не показывается
AUTO_SLUG = "auto"


@dataclass(frozen=True)
class DocType:
    slug: str
    version: int
    title: str
    description: str
    builtin: bool


async def list_doc_types(session: AsyncSession, tenant_id: UUID | None) -> list[DocType]:
    items: dict[str, DocType] = {
        slug: DocType(slug, s.version, s.title, s.description, True) for slug, s in builtin_schemas().items()
    }
    rows = (
        await session.execute(
            select(SchemaRecord.tenant_id, SchemaRecord.slug, SchemaRecord.version, SchemaRecord.body)
            .where(or_(SchemaRecord.tenant_id.is_(None), SchemaRecord.tenant_id == tenant_id))
            # встроенные раньше своих, внутри — по возрастанию версии: последняя запись побеждает
            .order_by(SchemaRecord.tenant_id.is_not(None), SchemaRecord.version)
        )
    ).all()
    for owner, slug, version, body in rows:
        builtin = owner is None
        current = items.get(slug)
        if builtin and current is not None and current.version > version:
            continue
        items[slug] = DocType(slug, version, body.get("title", slug), body.get("description", ""), builtin)
    return sorted((t for t in items.values() if t.slug != AUTO_SLUG), key=lambda t: t.slug)


_TRAILING_PARENS = re.compile(r"\s*\([^()]*\)$")


def normalize_title(text: str) -> str:
    return " ".join(text.split()).casefold().replace("ё", "е")


def match_doc_type(text: str | None, types: list[DocType]) -> str | None:
    """Slug типа, у которого название или slug равны тексту (без регистра, пробелов и ё); при нескольких — первый.

    Точное совпадение важнее совпадения с названием без уточнения в конце: «Счёт на оплату (РФ)» → «счет на оплату».
    """
    if not text or not text.strip():
        return None
    wanted = normalize_title(text)
    ordered = sorted(types, key=lambda t: t.slug)
    for t in ordered:
        if wanted in (normalize_title(t.title), normalize_title(t.slug)):
            return t.slug
    for t in ordered:
        if wanted == _TRAILING_PARENS.sub("", normalize_title(t.title)):
            return t.slug
    return None
