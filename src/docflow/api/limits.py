"""Потолок тела POST /v1/documents до разбора multipart.

FastAPI разбирает форму (а Starlette пишет файлы на диск) раньше, чем выполняется зависимость авторизации,
поэтому без этого слоя любой без ключа мог бы залить диск. Авторизацию здесь не делаем: размера достаточно.
"""

import json

from docflow.intake import request_limit_bytes

LIMITED = {("POST", "/v1/documents")}


class _TooLarge(Exception):
    pass


class RequestSizeLimit:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or (scope["method"], scope["path"].rstrip("/")) not in LIMITED:
            await self.app(scope, receive, send)
            return
        settings = scope["app"].state.settings
        limit = request_limit_bytes(settings)
        detail = f"Запрос больше {settings.max_request_mb} МБ"

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject(send, detail)
            return

        # тело без Content-Length (chunked) или с заниженным: считаем байты по мере чтения
        received = 0
        exceeded = False
        started = False

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _TooLarge()
            return message

        async def guarded_send(message):
            nonlocal started
            if exceeded:
                # FastAPI превращает ошибку чтения тела в свой 400 — его глушим, ответим 413 сами
                return
            started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await _reject(send, detail)


async def _reject(send, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
