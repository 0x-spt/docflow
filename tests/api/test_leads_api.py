import json

import httpx
import pytest
import respx
from sqlalchemy import select

from docflow.db.models import Lead

GOOD = {
    "name": "Иван",
    "company": "ООО Ромашка",
    "contact": "@ivan",
    "niche": "автосервис",
    "volume": "100-1000",
    "documents": "заказ-наряды",
    "message": "хотим аудит",
    "consent": True,
}


async def _leads(api) -> list[Lead]:
    async with api.sessionmaker() as s:
        return list((await s.execute(select(Lead))).scalars())


async def test_lead_created(api):
    r = await api.client.post("/v1/leads", json=GOOD)
    assert r.status_code == 201
    assert set(r.json()) == {"id"}
    [lead] = await _leads(api)
    assert str(lead.id) == r.json()["id"]
    assert lead.name == "Иван" and lead.volume == "100-1000" and lead.notified is False
    assert lead.ip  # адрес берётся из соединения


async def test_lead_strips_and_nulls_empty(api):
    body = {"name": "  Иван ", "contact": " 123 ", "volume": "unknown", "consent": True, "company": "  ", "message": ""}
    assert (await api.client.post("/v1/leads", json=body)).status_code == 201
    [lead] = await _leads(api)
    assert lead.name == "Иван" and lead.contact == "123"
    assert lead.company is None and lead.message is None and lead.niche is None


@pytest.mark.parametrize("consent", [False, None])
async def test_lead_requires_consent(api, consent):
    body = {**GOOD, "consent": consent}
    r = await api.client.post("/v1/leads", json=body)
    assert r.status_code == 422
    assert "нужно согласие на обработку персональных данных" in r.text
    assert await _leads(api) == []


async def test_lead_consent_missing(api):
    body = {k: v for k, v in GOOD.items() if k != "consent"}
    r = await api.client.post("/v1/leads", json=body)
    assert r.status_code == 422


@pytest.mark.parametrize(
    "patch",
    [
        {"name": ""},
        {"name": "   "},
        {"contact": "ab"},
        {"volume": "много"},
        {"message": "я" * 2001},
    ],
)
async def test_lead_validation(api, patch):
    r = await api.client.post("/v1/leads", json={**GOOD, **patch})
    assert r.status_code == 422
    assert await _leads(api) == []


async def test_honeypot_not_stored(api):
    r = await api.client.post("/v1/leads", json={**GOOD, "website": "http://spam"})
    assert r.status_code == 201 and r.json()["id"]
    assert await _leads(api) == []


async def test_lead_rate_limit_per_ip(api):
    for _ in range(5):
        assert (await api.client.post("/v1/leads", json=GOOD)).status_code == 201
    r = await api.client.post("/v1/leads", json=GOOD)
    assert r.status_code == 429
    assert len(await _leads(api)) == 5


async def test_invalid_does_not_consume_slot(api):
    for _ in range(10):
        assert (await api.client.post("/v1/leads", json={**GOOD, "name": ""})).status_code == 422
    assert (await api.client.post("/v1/leads", json=GOOD)).status_code == 201


async def test_honeypot_consumes_slot(api):
    for _ in range(5):
        assert (await api.client.post("/v1/leads", json={**GOOD, "website": "x"})).status_code == 201
    assert (await api.client.post("/v1/leads", json=GOOD)).status_code == 429


@respx.mock
async def test_lead_notifies_telegram(api):
    api.settings.leads_tg_token = "TOK"
    api.settings.leads_tg_chat = "42"
    route = respx.post("https://api.telegram.org/botTOK/sendMessage").mock(return_value=httpx.Response(200, json={}))
    r = await api.client.post("/v1/leads", json=GOOD)
    assert r.status_code == 201
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent["chat_id"] == "42" and "parse_mode" not in sent
    for part in ("Иван", "ООО Ромашка", "@ivan", "автосервис", "100-1000", "заказ-наряды", "хотим аудит"):
        assert part in sent["text"]
    [lead] = await _leads(api)
    assert lead.ip not in sent["text"]
    [lead] = await _leads(api)
    assert lead.notified is True


@respx.mock
async def test_lead_telegram_failure_still_201(api):
    api.settings.leads_tg_token = "TOK"
    api.settings.leads_tg_chat = "42"
    respx.post("https://api.telegram.org/botTOK/sendMessage").mock(return_value=httpx.Response(500))
    r = await api.client.post("/v1/leads", json=GOOD)
    assert r.status_code == 201
    [lead] = await _leads(api)
    assert lead.notified is False


@respx.mock
async def test_lead_without_telegram_settings_makes_no_request(api):
    route = respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(return_value=httpx.Response(200))
    assert (await api.client.post("/v1/leads", json=GOOD)).status_code == 201
    assert not route.called


async def test_lead_targets_saved_and_optional(api):
    r = await api.client.post("/v1/leads", json={**GOOD, "targets": ["1c", "excel"]})
    assert r.status_code == 201
    assert (await api.client.post("/v1/leads", json=GOOD)).status_code == 201  # без targets
    assert {tuple(lead.targets) for lead in await _leads(api)} == {("1c", "excel"), ()}


@pytest.mark.parametrize(
    "targets",
    [
        ["sap"],
        ["1c", "1c"],
        ["1c", "bitrix24", "amocrm", "google_sheets", "excel", "own_system", "unknown", "1c"],
        "1c",
    ],
)
async def test_lead_targets_invalid_422(api, targets):
    r = await api.client.post("/v1/leads", json={**GOOD, "targets": targets})
    assert r.status_code == 422


async def test_lead_targets_null_is_empty(api):
    r = await api.client.post("/v1/leads", json={**GOOD, "targets": None})
    assert r.status_code == 201
    [lead] = await _leads(api)
    assert lead.targets == []
