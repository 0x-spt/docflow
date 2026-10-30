"""Клиент Telegram Bot API. Токен лежит в пути запроса, поэтому он не попадает ни в ошибки, ни в логи."""

import json
import logging
import re

import httpx

# httpx на INFO пишет URL целиком; канальный процесс не вызывает create_app, поэтому закрываем и здесь
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN_RE = re.compile(r"^\d{3,20}:[A-Za-z0-9_-]{30,100}$", re.ASCII)
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
DOWNLOAD_CHUNK = 64 * 1024


class BotApiError(Exception):
    """Сбой Bot API. В message нет токена и адреса запроса."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class BotTokenRevoked(BotApiError):
    """401 или 404: токен отозван или бота удалили."""


class BotConflict(BotApiError):
    """409 у getUpdates: параллельно идёт другой опрос или установлен вебхук."""


class BotFileTooBig(BotApiError):
    """Файл больше допустимого."""


class BotApi:
    def __init__(self, http: httpx.AsyncClient, token: str, base_url: str = "https://api.telegram.org"):
        self._http = http
        self._token = token
        self._base = base_url.rstrip("/")

    def _clean(self, text: str) -> str:
        return text.replace(self._token, "***")

    async def _call(
        self,
        method: str,
        payload: dict | None = None,
        *,
        data: dict | None = None,
        files: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> object:
        url = f"{self._base}/bot{self._token}/{method}"
        try:
            if files is not None:
                resp = await self._http.post(url, data=data, files=files, timeout=timeout)
            else:
                resp = await self._http.post(url, json=payload or {}, timeout=timeout)
        except httpx.HTTPError as exc:
            # в тексте исключения может быть URL с токеном: наружу только тип
            raise BotApiError(f"Telegram недоступен ({type(exc).__name__})", retryable=True) from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code == 200 and isinstance(body, dict) and body.get("ok"):
            return body.get("result")
        raise self._error(method, resp.status_code, body)

    def _error(self, method: str, status: int, body: object) -> BotApiError:
        description = body.get("description") if isinstance(body, dict) else None
        text = f"Telegram {method}: {status}"
        if isinstance(description, str) and description:
            text += f" {self._clean(description)[:200]}"
        if status in (401, 404):
            return BotTokenRevoked(text)
        if status == 409:
            return BotConflict(text)
        return BotApiError(text, retryable=status == 429 or status >= 500)

    async def get_me(self) -> dict:
        return await self._call("getMe")  # type: ignore[return-value]

    async def delete_webhook(self) -> None:
        await self._call("deleteWebhook")

    async def get_updates(self, offset: int, timeout: int) -> list[dict]:
        result = await self._call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 10,
        )
        return result  # type: ignore[return-value]

    async def download(self, file_id: str, limit: int = MAX_DOWNLOAD_BYTES) -> bytes:
        info = await self._call("getFile", {"file_id": file_id})
        path = info.get("file_path") if isinstance(info, dict) else None
        if not isinstance(path, str) or not path:
            raise BotApiError("Telegram не вернул путь к файлу")
        if isinstance(info.get("file_size"), int) and info["file_size"] > limit:
            raise BotFileTooBig("файл слишком большой")
        url = f"{self._base}/file/bot{self._token}/{path}"
        buf = bytearray()
        try:
            async with self._http.stream("GET", url, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status_code != 200:
                    raise self._error("download", resp.status_code, None)
                async for chunk in resp.aiter_bytes(DOWNLOAD_CHUNK):
                    buf += chunk
                    if len(buf) > limit:
                        raise BotFileTooBig("файл слишком большой")
        except httpx.HTTPError as exc:
            raise BotApiError(f"Telegram недоступен ({type(exc).__name__})", retryable=True) from None
        return bytes(buf)

    @staticmethod
    def _reply(reply_to: int | None) -> dict:
        if reply_to is None:
            return {}
        return {"reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True}}

    async def send_message(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        await self._call("sendMessage", {"chat_id": chat_id, "text": text, **self._reply(reply_to)})

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str, reply_to: int | None = None
    ) -> None:
        # в multipart вложенные объекты передаются JSON-строкой
        data = {"chat_id": str(chat_id), "caption": caption}
        if reply_to is not None:
            data["reply_parameters"] = json.dumps(self._reply(reply_to)["reply_parameters"])
        await self._call("sendDocument", data=data, files={"document": (filename, content)})
