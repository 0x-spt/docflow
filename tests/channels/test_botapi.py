import httpx
import pytest
from fake_telegram import FakeTelegram

from docflow.channels.botapi import BotApi, BotApiError, BotConflict, BotFileTooBig, BotTokenRevoked

TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
async def bot(tg):
    async with httpx.AsyncClient(transport=tg.transport) as http:
        yield BotApi(http, TOKEN)


def assert_clean(exc: BotApiError):
    assert TOKEN not in str(exc) and TOKEN not in exc.message
    assert "api.telegram.org" not in exc.message and "/bot" not in exc.message


async def test_errors_have_no_token(bot, tg):
    tg.fail_with["getMe"] = httpx.ConnectError(f"cannot connect to https://api.telegram.org/bot{TOKEN}/getMe")
    with pytest.raises(BotApiError) as e:
        await bot.get_me()
    assert_clean(e.value)
    assert e.value.retryable

    tg.fail_with["getMe"] = (500, {"ok": False, "description": f"oops {TOKEN}"})
    with pytest.raises(BotApiError) as e:
        await bot.get_me()
    assert_clean(e.value)
    assert e.value.retryable

    tg.fail_with["getMe"] = 401
    with pytest.raises(BotTokenRevoked) as e:
        await bot.get_me()
    assert_clean(e.value)
    assert not e.value.retryable


async def test_404_is_revoked_and_409_is_conflict(bot, tg):
    tg.fail_with["getMe"] = 404
    with pytest.raises(BotTokenRevoked):
        await bot.get_me()
    tg.fail_with["getUpdates"] = 409
    with pytest.raises(BotConflict):
        await bot.get_updates(0, 0)


async def test_429_retryable_403_not(bot, tg):
    tg.fail_with["sendMessage"] = 429
    with pytest.raises(BotApiError) as e:
        await bot.send_message(1, "x")
    assert e.value.retryable
    tg.fail_with["sendMessage"] = 403
    with pytest.raises(BotApiError) as e:
        await bot.send_message(1, "x")
    assert not e.value.retryable
    assert not isinstance(e.value, BotTokenRevoked)
    tg.fail_with["sendMessage"] = 400
    with pytest.raises(BotApiError) as e:
        await bot.send_message(1, "x")
    assert not e.value.retryable


async def test_get_updates_without_offset_omits_it(bot, tg):
    await bot.get_updates(None, 0)
    assert "offset" not in tg.calls[-1][1]


async def test_get_updates_payload(bot, tg):
    tg.updates = [{"update_id": 5, "message": {}}, {"update_id": 7, "message": {}}]
    got = await bot.get_updates(6, 30)
    assert [u["update_id"] for u in got] == [7]
    assert tg.calls[-1] == ("getUpdates", {"offset": 6, "timeout": 30, "allowed_updates": ["message"]})


async def test_download(bot, tg):
    tg.files["f1"] = b"%PDF-data"
    assert await bot.download("f1", 1000) == b"%PDF-data"


@pytest.mark.parametrize("report_size", [True, False])
async def test_download_cuts_off_over_limit(bot, tg, report_size):
    tg.report_size = report_size
    tg.files["big"] = b"x" * 5000
    with pytest.raises(BotFileTooBig) as e:
        await bot.download("big", 1000)
    assert_clean(e.value)


async def test_get_file_over_bot_api_limit_is_too_big(bot, tg):
    tg.fail_with["getFile"] = (400, {"ok": False, "description": "Bad Request: file is too big"})
    with pytest.raises(BotFileTooBig) as e:
        await bot.download("big")
    assert_clean(e.value)
    assert e.value.retryable is False


async def test_reply_parameters_sent(bot, tg):
    await bot.send_message(42, "привет", reply_to=9)
    assert tg.calls[-1] == (
        "sendMessage",
        {"chat_id": 42, "text": "привет", "reply_parameters": {"message_id": 9, "allow_sending_without_reply": True}},
    )
    await bot.send_message(42, "без ответа")
    assert "reply_parameters" not in tg.calls[-1][1]


async def test_send_document_multipart(bot, tg):
    await bot.send_document(42, "doc.pdf", b"%PDF-1", "Готово", reply_to=9)
    method, payload = tg.calls[-1]
    assert method == "sendDocument"
    assert payload["content_type"].startswith("multipart/form-data")
    raw = payload["multipart"]
    assert b'name="chat_id"' in raw and b"42" in raw
    assert b'name="document"; filename="doc.pdf"' in raw and b"%PDF-1" in raw
    assert "Готово".encode() in raw
    assert b'"message_id": 9' in raw and b"allow_sending_without_reply" in raw


async def test_download_404_is_not_revoked(bot, tg):
    # getFile отвечает, а самого файла нет: токен жив
    tg.report_size = False
    with pytest.raises(BotApiError) as e:
        await bot.download("absent", 1000)
    assert not isinstance(e.value, BotTokenRevoked) and not e.value.retryable
    assert_clean(e.value)
    tg.fail_with["download"] = httpx.ConnectError("x")
    with pytest.raises(BotApiError):
        await bot.download("absent", 1000)
