import os
import socket
import uuid
from urllib.parse import urlparse

import pytest

from docflow.config import Settings
from docflow.storage.blobs import (
    LocalBlobStore,
    S3BlobStore,
    blob_store_from_settings,
    original_key,
    scan_key,
)


async def test_put_get_exists(tmp_path):
    store = LocalBlobStore(tmp_path)
    assert not await store.exists("docs/1/scan.pdf")
    await store.put("docs/1/scan.pdf", b"%PDF", "application/pdf")
    assert await store.exists("docs/1/scan.pdf")
    assert await store.get("docs/1/scan.pdf") == b"%PDF"


async def test_put_overwrites_and_leaves_no_temp_files(tmp_path):
    store = LocalBlobStore(tmp_path)
    await store.put("a/b.bin", b"one", "x/y")
    await store.put("a/b.bin", b"two", "x/y")
    assert await store.get("a/b.bin") == b"two"
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["b.bin"]


async def test_delete_removes_and_ignores_missing(tmp_path):
    store = LocalBlobStore(tmp_path)
    await store.put("docs/1/scan.pdf", b"%PDF", "application/pdf")
    await store.delete("docs/1/scan.pdf")
    assert not await store.exists("docs/1/scan.pdf")
    await store.delete("docs/1/scan.pdf")
    await store.delete("nope/x")
    with pytest.raises(ValueError):
        await store.delete("../x")


async def test_get_missing_raises_keyerror(tmp_path):
    with pytest.raises(KeyError):
        await LocalBlobStore(tmp_path).get("nope/x")


@pytest.mark.parametrize(
    "key",
    ["", "../x", "a/../../x", "a/..", "/abs", "a\\b", "C:/x", "C:x", "a//b", "a/./b"],
)
async def test_bad_keys_rejected(tmp_path, key):
    store = LocalBlobStore(tmp_path)
    with pytest.raises(ValueError):
        await store.put(key, b"x", "text/plain")
    with pytest.raises(ValueError):
        await store.get(key)
    with pytest.raises(ValueError):
        await store.exists(key)


def test_original_key_format():
    assert original_key("abc", 2, "scan.jpg") == "docs/abc/original/2_scan.jpg"


def test_original_key_sanitizes_filename():
    assert original_key("d", 1, "../../etc/pass wd.png") == "docs/d/original/1_pass_wd.png"
    assert original_key("d", 1, "C:\\x\\акт №1.pdf") == "docs/d/original/1_акт__1.pdf"
    key = original_key("d", 1, "a" * 500)
    assert len(key.rsplit("/", 1)[1]) <= len("1_") + 100


def test_original_key_empty_name_still_safe():
    assert original_key("d", 3, "") == "docs/d/original/3_file"
    assert original_key("d", 3, "..") == "docs/d/original/3_file"


def test_scan_key():
    assert scan_key("abc") == "docs/abc/scan.pdf"


def test_factory_local(tmp_path):
    s = Settings(blob_backend="local", blob_root=tmp_path)
    assert isinstance(blob_store_from_settings(s), LocalBlobStore)


def test_factory_s3():
    s = Settings(blob_backend="s3", s3_endpoint="http://x:9000", s3_access_key="a", s3_secret_key="b")
    assert isinstance(blob_store_from_settings(s), S3BlobStore)


def test_factory_s3_requires_endpoint():
    with pytest.raises(ValueError):
        blob_store_from_settings(Settings(blob_backend="s3", s3_endpoint=None))


def test_s3_client_has_timeouts_and_no_default_checksums():
    config = S3BlobStore("http://x:9000", "b", "a", "s")._client.meta.config
    assert config.connect_timeout == 5 and config.read_timeout == 60
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"
    assert config.retries["mode"] == "standard"


def _s3_endpoint() -> str:
    # по умолчанию — SeaweedFS из docker-compose.yml
    return os.environ.get("DOCFLOW_S3_ENDPOINT", "http://localhost:8333")


def _reachable(endpoint: str) -> bool:
    u = urlparse(endpoint)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.integration
async def test_s3_roundtrip():
    endpoint = _s3_endpoint()
    if not _reachable(endpoint):
        pytest.skip(f"S3 недоступен: {endpoint}")
    store = S3BlobStore(
        endpoint,
        f"docflow-test-{uuid.uuid4().hex[:8]}",
        os.environ.get("DOCFLOW_S3_ACCESS_KEY", "docflow"),
        os.environ.get("DOCFLOW_S3_SECRET_KEY", "docflow-secret"),
    )
    key = scan_key("t1")
    assert not await store.exists(key)
    await store.put(key, b"%PDF", "application/pdf")
    assert await store.exists(key)
    assert await store.get(key) == b"%PDF"
    with pytest.raises(KeyError):
        await store.get("docs/none/scan.pdf")
    await store.delete(key)
    assert not await store.exists(key)
    await store.delete(key)
    with pytest.raises(ValueError):
        await store.put("../x", b"x", "text/plain")
