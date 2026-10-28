"""Синтетические файлы для тестов API."""

import io
import uuid
from datetime import UTC, datetime, timedelta

import img2pdf
from PIL import Image
from sqlalchemy import select

from docflow.auth import generate_session_token
from docflow.db.models import ApiKey, User, hash_key
from docflow.db.models import Session as UserSession

BASE_URL = "https://api.example.test"


async def session_for(api, key_headers: dict[str, str], role: str = "owner") -> tuple[dict[str, str], uuid.UUID]:
    """Пользователь тенанта, чей ключ в key_headers, и его сессия: (заголовки, id пользователя)."""
    key_hash = hash_key(key_headers["Authorization"][7:])
    async with api.sessionmaker() as s:
        tenant_id = (await s.execute(select(ApiKey.tenant_id).where(ApiKey.key_hash == key_hash))).scalar_one()
        user = User(tenant_id=tenant_id, email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x", role=role)
        s.add(user)
        await s.flush()
        token, token_hash = generate_session_token()
        s.add(UserSession(user_id=user.id, token_hash=token_hash, expires_at=datetime.now(UTC) + timedelta(days=1)))
        await s.commit()
        return {"Authorization": f"Bearer {token}"}, user.id


def png_bytes(size=(40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def pdf_bytes(pages: int = 2) -> bytes:
    return img2pdf.convert([png_bytes() for _ in range(pages)])
