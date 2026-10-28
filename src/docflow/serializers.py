"""Единое представление документа: и для GET /v1/documents/{id}, и для тела вебхука."""

from docflow.db.models import Document


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def document_payload(document: Document, base_url: str) -> dict:
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
    }
