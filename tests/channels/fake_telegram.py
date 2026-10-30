"""Заглушка Telegram Bot API на httpx.MockTransport: для тестов клиента, API и канального процесса."""

import json
import re

import httpx

BOT_PATH = re.compile(r"^/bot[^/]+/(?P<method>\w+)$")
FILE_PATH = re.compile(r"^/file/bot[^/]+/(?P<path>.+)$")


class FakeTelegram:
    """updates — очередь входящих; files — file_id → байты; calls — (метод, тело) каждого запроса.

    fail_with: метод (или "*") → число (HTTP-статус), (статус, тело) или исключение, которое будет выброшено.
    fail_files: file_id, на которых getFile отвечает 500 (сбой одного файла, а не всего метода).
    on_call: вызывается с именем метода при каждом запросе, чтобы проверить состояние в этот момент.
    """

    def __init__(self, username: str = "acme_bot"):
        self.username = username
        self.updates: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, object]] = []
        self.fail_with: dict[str, object] = {}
        # False: getFile не сообщает размер, и клиент обязан резать поток сам
        self.report_size = True
        self.fail_files: set[str] = set()
        self.on_call = None
        self.transport = httpx.MockTransport(self._handle)

    def _failure(self, method: str):
        return self.fail_with.get(method, self.fail_with.get("*"))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        m = FILE_PATH.match(request.url.path)
        if m:
            self.calls.append(("download", m["path"]))
            if self.on_call:
                self.on_call("download")
            fail = self._failure("download")
            if isinstance(fail, Exception):
                raise fail
            file_id = m["path"].removeprefix("documents/")
            if file_id not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[file_id])
        m = BOT_PATH.match(request.url.path)
        if not m:
            return httpx.Response(404, json={"ok": False, "description": "Not Found"})
        method = m["method"]
        if request.headers.get("content-type", "").startswith("application/json"):
            payload: object = json.loads(request.content or b"{}")
        else:
            payload = {"multipart": request.content, "content_type": request.headers.get("content-type")}
        self.calls.append((method, payload))
        if self.on_call:
            self.on_call(method)
        if method == "getFile" and payload["file_id"] in self.fail_files:
            return httpx.Response(500, json={"ok": False, "description": "failure"})
        fail = self._failure(method)
        if isinstance(fail, Exception):
            raise fail
        if isinstance(fail, int):
            return httpx.Response(fail, json={"ok": False, "description": "failure"})
        if isinstance(fail, tuple):
            return httpx.Response(fail[0], json=fail[1])
        return httpx.Response(200, json={"ok": True, "result": self._result(method, payload)})

    def _result(self, method: str, payload) -> object:
        if method == "getMe":
            return {"id": 1, "is_bot": True, "username": self.username}
        if method == "getUpdates":
            return [u for u in self.updates if u["update_id"] >= payload.get("offset", 0)]
        if method == "getFile":
            file_id = payload["file_id"]
            info = {"file_id": file_id, "file_path": f"documents/{file_id}"}
            if self.report_size:
                info["file_size"] = len(self.files.get(file_id, b""))
            return info
        return True
