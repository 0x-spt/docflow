"""Асинхронное хранилище больших файлов: локальная ФС (dev, тесты) и S3/MinIO (прод)."""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from docflow.config import Settings


class BlobNotFound(KeyError):
    """Ключа нет в хранилище."""


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...


# --- ключи ---------------------------------------------------------------

_BAD_CHARS = re.compile(r"[^\w.\-]")


def validate_key(key: str) -> str:
    """Ключ — относительный путь через "/" без выхода за корень. Иначе ValueError."""
    if not key or key.startswith("/") or "\\" in key or "\0" in key:
        raise ValueError(f"недопустимый ключ: {key!r}")
    for part in key.split("/"):
        # пустой и "." сегменты не нужны никому; ":" закрывает диски Windows ("C:x")
        if part in ("", ".", "..") or ":" in part:
            raise ValueError(f"недопустимый ключ: {key!r}")
    return key


def _safe_filename(filename: str) -> str:
    base = re.split(r"[\\/]", filename)[-1]
    base = _BAD_CHARS.sub("_", base)[:100]
    if not base.strip("."):
        return "file"
    return base


def original_key(document_id: str, n: int, filename: str) -> str:
    return f"docs/{document_id}/original/{n}_{_safe_filename(filename)}"


def scan_key(document_id: str) -> str:
    return f"docs/{document_id}/scan.pdf"


# --- локальная ФС --------------------------------------------------------


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self._root / key).resolve()
        # защита в глубину: даже если валидатор что-то пропустит, наружу не выйдем
        if not path.is_relative_to(self._root) or path == self._root:
            raise ValueError(f"ключ выходит за корень хранилища: {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # временный файл в том же каталоге, чтобы os.replace был атомарным
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise BlobNotFound(key) from None

    async def exists(self, key: str) -> bool:
        path = self._path(key)
        return await asyncio.to_thread(path.is_file)


# --- S3 / MinIO ----------------------------------------------------------

_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


class S3BlobStore:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
        )
        self._lock = asyncio.Lock()
        self._bucket_ready = False

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        async with self._lock:
            if self._bucket_ready:
                return
            try:
                await asyncio.to_thread(self._client.create_bucket, Bucket=self._bucket)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                    raise
            self._bucket_ready = True

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        validate_key(key)
        await self._ensure_bucket()
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        validate_key(key)
        await self._ensure_bucket()
        try:
            resp = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
            return await asyncio.to_thread(resp["Body"].read)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in _MISSING_CODES:
                raise BlobNotFound(key) from None
            raise

    async def exists(self, key: str) -> bool:
        validate_key(key)
        await self._ensure_bucket()
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in _MISSING_CODES:
                return False
            raise


def blob_store_from_settings(s: Settings) -> BlobStore:
    if s.blob_backend == "local":
        return LocalBlobStore(s.blob_root)
    if not (s.s3_endpoint and s.s3_access_key and s.s3_secret_key):
        raise ValueError("для blob_backend=s3 нужны s3_endpoint, s3_access_key и s3_secret_key")
    return S3BlobStore(s.s3_endpoint, s.s3_bucket, s.s3_access_key, s.s3_secret_key)
