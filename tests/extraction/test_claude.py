import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from docflow.extraction.base import ExtractionError, system_prompt
from docflow.extraction.claude import ClaudeExtractor
from docflow.extraction.fake import FakeExtractor
from docflow.schemas.model import DocSchema

SCHEMA = DocSchema.model_validate(
    {"slug": "t_doc", "title": "Счёт", "description": "Счёт на оплату", "fields": [{"name": "total", "type": "number"}]}
)


def _response(blocks, stop_reason="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
    )


def _client(response=None, exc=None):
    client = MagicMock()
    if exc:
        client.beta.messages.create.side_effect = exc
    else:
        client.beta.messages.create.return_value = response
    return client


def _text(s):
    return SimpleNamespace(type="text", text=s)


def test_request_shape_and_parse():
    resp = _response(
        [SimpleNamespace(type="thinking", thinking="..."), SimpleNamespace(type="fallback"), _text('{"total": 5}')],
        model="claude-other",
    )
    client = _client(resp)
    ex = ClaudeExtractor(client=client)
    out = ex.extract(SCHEMA, [b"a", b"b", b"c"])
    assert out.raw == {"total": 5} and out.model == "claude-other"
    assert (out.input_tokens, out.output_tokens) == (11, 7)
    kw = client.beta.messages.create.call_args.kwargs
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "high"
    assert kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["max_tokens"] == 16000
    assert "thinking" not in kw and "temperature" not in kw
    content = kw["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "image", "text"]
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert [b["source"]["data"] for b in content[:3]] == [base64.b64encode(x).decode() for x in (b"a", b"b", b"c")]
    assert "3" in content[-1]["text"]
    assert kw["system"] == system_prompt(SCHEMA)


@pytest.mark.parametrize(
    "stop,reason",
    [("refusal", "refusal"), ("max_tokens", "max_tokens"), ("model_context_window_exceeded", "max_tokens"),
     ("pause_turn", "api_error")],
)
def test_stop_reasons_raise(stop, reason):
    ex = ClaudeExtractor(client=_client(_response([_text("{}")], stop_reason=stop)))
    with pytest.raises(ExtractionError) as e:
        ex.extract(SCHEMA, [b"a"])
    assert e.value.reason == reason


def test_invalid_json():
    ex = ClaudeExtractor(client=_client(_response([_text("not json")])))
    with pytest.raises(ExtractionError) as e:
        ex.extract(SCHEMA, [b"a"])
    assert e.value.reason == "invalid_json"


def test_api_errors_wrapped():
    req = httpx.Request("POST", "https://x")
    excs = [
        anthropic.APIConnectionError(request=req),
        anthropic.APIResponseValidationError(response=httpx.Response(200, request=req), body=None),
        anthropic.BadRequestError("bad", response=httpx.Response(400, request=req), body=None),
    ]
    for exc in excs:
        ex = ClaudeExtractor(client=_client(exc=exc))
        with pytest.raises(ExtractionError) as e:
            ex.extract(SCHEMA, [b"a"])
        assert e.value.reason == "api_error" and e.value.__cause__ is exc


def test_system_prompt_deterministic_and_has_title():
    p = system_prompt(SCHEMA)
    assert p == system_prompt(SCHEMA)
    assert "Счёт" in p and "Счёт на оплату" in p and "_uncertain" in p


def test_fake():
    ex = FakeExtractor({"t_doc": {"total": 1}})
    assert ex.extract(SCHEMA, []).raw == {"total": 1}


def test_default_client_fits_job_timeout(monkeypatch):
    """Свой клиент SDK: таймаут и повторы укладываются в job_timeout воркера, а не SDK-шные 10 минут × 3."""
    from docflow.worker import JOB_TIMEOUT

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    ex = ClaudeExtractor()
    assert ex.client.timeout == 240.0 < JOB_TIMEOUT
    assert ex.client.max_retries == 1
