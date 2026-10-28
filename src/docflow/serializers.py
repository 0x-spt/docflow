"""Единое представление документа: и для GET /v1/documents/{id}, и для тела вебхука."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import Destination, Document, Export


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def export_item(export: Export, destination_name: str | None, kind: str | None) -> dict:
    """Выгрузка в ответе: получатель мог быть удалён — тогда destination_id/имя/тип пустые."""
    return {
        "id": str(export.id),
        "destination_id": str(export.destination_id) if export.destination_id else None,
        "destination_name": destination_name,
        "kind": kind,
        "purpose": export.purpose,
        "status": export.status,
        "external_id": export.external_id,
        "error": export.last_error,
        "updated_at": _iso(export.updated_at),
    }


async def document_exports(session: AsyncSession, document_id: UUID) -> list[dict]:
    """Выгрузки документа одним запросом (с именем и типом получателя), старые первыми."""
    rows = await session.execute(
        select(Export, Destination.name, Destination.kind)
        .outerjoin(Destination, Destination.id == Export.destination_id)
        .where(Export.document_id == document_id)
        .order_by(Export.created_at, Export.id)
    )
    return [export_item(e, name, kind) for e, name, kind in rows.all()]


def document_payload(document: Document, base_url: str, exports: list[dict] | None = None) -> dict:
    ready = document.status in ("ok", "needs_review")
    return {
        "id": str(document.id),
        "status": document.status,
        "schema": {"slug": document.schema_slug, "version": document.schema_version},
        "data": document.data,
        "issues": [
            {"field": i.get("field"), "code": i.get("code"), "message": i.get("message")}
            for i in (document.issues or [])
        ],
        "error": document.error,
        "pages": document.pages,
        "model": document.model,
        "created_at": _iso(document.created_at),
        "finished_at": _iso(document.finished_at),
        "review": {"at": _iso(document.reviewed_at), "note": document.review_note} if document.reviewed_at else None,
        "pdf_url": f"{base_url.rstrip('/')}/v1/documents/{document.id}/pdf" if ready else None,
        "digital_pdf_url": f"{base_url.rstrip('/')}/v1/documents/{document.id}/digital.pdf" if ready else None,
        "exports": exports if exports is not None else [],
    }
