"""Заявки на аудит с публичного сайта: приём без авторизации, уведомление оператору в Telegram."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, field_validator

from docflow.api.deps import SessionDep, SettingsDep
from docflow.api.ratelimit import client_ip
from docflow.db.models import Lead
from docflow.notify import notify_lead

router = APIRouter(tags=["leads"])

TOO_MANY = "слишком много заявок с вашего адреса, попробуйте позже"
NO_CONSENT = "нужно согласие на обработку персональных данных"


def _empty_to_none(value):
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _opt(max_length: int):
    """Необязательная строка: пробелы по краям срезаются, пустая становится NULL."""
    return Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=max_length)] | None,
        BeforeValidator(_empty_to_none),
    ]


class LeadIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    company: _opt(200) = None
    contact: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    niche: _opt(100) = None
    volume: Literal["lt100", "100-1000", "1000-10000", "gt10000", "unknown"]
    documents: _opt(2000) = None
    message: _opt(2000) = None
    # без поля, с null или false — одно и то же сообщение
    consent: bool = Field(default=False, validate_default=True)
    # приманка для ботов: настоящая форма держит поле пустым и невидимым
    website: str | None = Field(default=None, max_length=2000)

    @field_validator("consent", mode="before")
    @classmethod
    def _consent_given(cls, v: object) -> bool:
        if v is not True:
            raise ValueError(NO_CONSENT)
        return v


async def _notify(app: FastAPI, lead_id: uuid.UUID) -> None:
    """Фоновая отправка: свой сеанс БД, т.к. сеанс запроса к этому моменту закрыт."""
    async with app.state.sessionmaker() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None or not await notify_lead(lead, app.state.settings):
            return
        lead.notified = True
        await session.commit()


@router.post(
    "/v1/leads",
    status_code=status.HTTP_201_CREATED,
    summary="Оставить заявку на аудит",
    description="Без авторизации. Не больше 5 заявок в час с одного адреса, дальше — 429.",
)
async def create_lead(
    body: LeadIn, request: Request, session: SessionDep, settings: SettingsDep, background: BackgroundTasks
) -> dict[str, str]:
    ip = client_ip(request, settings)
    # лимит учитывает и приманку: бот не должен получать бесконечные «успехи»; 422 слотов не тратят
    if not request.app.state.leads_limiter.hit(f"ip:{ip}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    if body.website:
        return {"id": str(uuid.uuid4())}
    lead = Lead(**body.model_dump(exclude={"consent", "website"}), ip=ip)
    session.add(lead)
    await session.commit()
    background.add_task(_notify, request.app, lead.id)
    return {"id": str(lead.id)}
