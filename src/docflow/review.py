"""Ручная проверка документа в статусе needs_review: подтвердить (с правками или без) или отклонить."""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.api.deps import Principal
from docflow.db.models import Document
from docflow.extraction.normalize import normalize
from docflow.pipeline import SchemaResolver, load_dictionaries
from docflow.schemas.llm import UNCERTAIN
from docflow.validation.dictionaries import resolve_dictionaries
from docflow.validation.engine import evaluate

REJECTED = "отклонён при проверке"


class ReviewConflict(Exception):
    """Документ уже не в needs_review: его проверил другой оператор или он ещё обрабатывается."""


async def _edited(
    session: AsyncSession, document: Document, data: dict, schemas: SchemaResolver
) -> tuple[dict, list[dict]]:
    """Правки оператора проходят тот же путь, что ответ модели: нормализация → справочники → проверки."""
    schema = await schemas.resolve(session, document.tenant_id, document.schema_slug, document.schema_version)
    dictionaries = await load_dictionaries(session, document.tenant_id, schema)
    fixed, issues = normalize(schema, data)
    issues += resolve_dictionaries(schema, fixed, dictionaries)
    issues += evaluate(schema, fixed)
    fixed.pop(UNCERTAIN, None)
    return fixed, [asdict(i) for i in issues]


async def review_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    principal: Principal,
    decision: Literal["approve", "reject"],
    data: dict | None,
    note: str | None,
    schemas: SchemaResolver,
) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.tenant_id != tenant_id or document.status != "needs_review":
        raise ReviewConflict
    values: dict = {"reviewed_by": principal.user_id, "reviewed_at": datetime.now(UTC), "review_note": note}
    if decision == "reject":
        values.update(status="failed", error=REJECTED + (f": {note}" if note else ""))
    else:
        values.update(status="ok")
        if data is not None:
            values["data"], values["issues"] = await _edited(session, document, data, schemas)
    # атомарно: два оператора одновременно — результат получит только один, второй увидит rowcount 0
    res = await session.execute(
        update(Document)
        .where(Document.id == document_id, Document.tenant_id == tenant_id, Document.status == "needs_review")
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    if res.rowcount != 1:
        raise ReviewConflict
    return await session.get(Document, document_id, populate_existing=True)
