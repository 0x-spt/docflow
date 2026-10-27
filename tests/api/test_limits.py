import json

from api_helpers import png_bytes
from sqlalchemy import func, select

from docflow.api.limits import RequestSizeLimit
from docflow.db.models import Document


async def _count(api) -> int:
    async with api.sessionmaker() as s:
        return (await s.execute(select(func.count()).select_from(Document))).scalar_one()


def _multipart(payload: bytes, boundary: str = "XyZ") -> bytes:
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"schema\"\r\n\r\ninvoice_ru\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.png\"\r\n"
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()


async def test_content_length_over_cap_413_without_auth(api):
    api.settings.max_request_mb = 1
    body = _multipart(b"\x89PNG" + b"\0" * (2 * 1024 * 1024))
    r = await api.client.post(
        "/v1/documents", content=body, headers={"Content-Type": "multipart/form-data; boundary=XyZ"}
    )
    # 413, а не 401: до авторизации и разбора тела дело не дошло
    assert r.status_code == 413
    assert "detail" in r.json()
    assert await _count(api) == 0


async def test_chunked_body_over_cap_413(api, auth):
    api.settings.max_request_mb = 1
    body = _multipart(b"\x89PNG" + b"\0" * (2 * 1024 * 1024))

    async def chunks():
        for i in range(0, len(body), 64 * 1024):
            yield body[i : i + 64 * 1024]

    r = await api.client.post(
        "/v1/documents",
        content=chunks(),
        headers={**auth, "Content-Type": "multipart/form-data; boundary=XyZ"},
    )
    assert "content-length" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 413
    assert "detail" in r.json()
    assert await _count(api) == 0
    assert api.enqueuer.calls == []


async def test_small_chunked_body_passes(api, auth):
    body = _multipart(png_bytes())

    async def chunks():
        yield body

    r = await api.client.post(
        "/v1/documents", content=chunks(), headers={**auth, "Content-Type": "multipart/form-data; boundary=XyZ"}
    )
    assert r.status_code == 202, r.text


async def test_middleware_rejects_before_reading_body():
    """Content-Length больше лимита: тело не читается, внутреннее приложение не вызывается."""

    class State:
        class settings:
            max_request_mb = 1

    class FakeApp:
        state = State

    async def inner(scope, receive, send):
        raise AssertionError("обработчик не должен вызываться")

    async def receive():
        raise AssertionError("тело не должно читаться")

    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/documents",
        "headers": [(b"content-length", str(5 * 1024 * 1024).encode())],
        "app": FakeApp,
    }
    await RequestSizeLimit(inner)(scope, receive, send)
    assert sent[0]["status"] == 413
    assert "detail" in json.loads(sent[1]["body"])


async def test_middleware_ignores_other_routes(api, auth):
    api.settings.max_request_mb = 0
    r = await api.client.post(
        "/v1/schemas",
        headers=auth,
        json={"slug": "x_doc", "title": "t", "description": "d", "fields": [
            {"name": "n", "type": "string", "description": "d"}]},
    )
    assert r.status_code == 201
