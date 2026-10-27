import base64
import json

import httpx
import pytest
import respx

from docflow.extraction.base import ExtractionError
from docflow.extraction.ollama import OllamaExtractor
from docflow.schemas.model import DocSchema

SCHEMA = DocSchema.model_validate(
    {"slug": "t_doc", "title": "Счёт", "description": "д", "fields": [{"name": "total", "type": "number"}]}
)
URL = "http://ollama:11434"


@respx.mock
def test_payload_has_schema_and_images():
    route = respx.post(f"{URL}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": '{"total": 1}'}})
    )
    OllamaExtractor(URL, "qwen2.5vl:7b").extract(SCHEMA, [b"x", b"y"])
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "qwen2.5vl:7b" and body["stream"] is False
    assert body["format"]["type"] == "object" and "total" in body["format"]["properties"]
    assert body["options"] == {"temperature": 0}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["images"] == [base64.b64encode(b"x").decode(), base64.b64encode(b"y").decode()]


@respx.mock
def test_parses_content():
    respx.post(f"{URL}/api/chat").mock(
        return_value=httpx.Response(
            200, json={"message": {"content": '{"total": 2}'}, "prompt_eval_count": 30, "eval_count": 4}
        )
    )
    out = OllamaExtractor(URL, "m").extract(SCHEMA, [b"x"])
    assert out.raw == {"total": 2} and out.model == "m"
    assert (out.input_tokens, out.output_tokens) == (30, 4)


@respx.mock
def test_errors_mapped():
    route = respx.post(f"{URL}/api/chat")
    route.mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ExtractionError) as e:
        OllamaExtractor(URL, "m").extract(SCHEMA, [b"x"])
    assert e.value.reason == "api_error"
    route.mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(ExtractionError) as e:
        OllamaExtractor(URL, "m").extract(SCHEMA, [b"x"])
    assert e.value.reason == "api_error"
    route.mock(return_value=httpx.Response(200, json={"message": {"content": "zzz"}}))
    with pytest.raises(ExtractionError) as e:
        OllamaExtractor(URL, "m").extract(SCHEMA, [b"x"])
    assert e.value.reason == "invalid_json"


def test_default_http_timeout_fits_job_timeout():
    from docflow.worker import JOB_TIMEOUT

    ex = OllamaExtractor("http://localhost:11434", "m")
    assert ex.http.timeout.read == 240.0 < JOB_TIMEOUT
