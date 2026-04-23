"""Cloudflare R2 (S3-compatible) wrapper.

Files are stored under `users/{user_id}/{folder}/{file_id}_{name}`.
All access is via signed URLs — bucket is private.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import boto3
from botocore.client import Config

from sahay.config import get_settings

settings = get_settings()

# boto3 is sync — we offload to a small executor.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="r2")

_endpoint = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=_endpoint,
        aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        region_name="auto",
    )


def _build_key(user_id: UUID, folder: str, file_id: UUID, name: str) -> str:
    safe_folder = folder.strip("/").replace("..", "")
    safe_name = name.replace("/", "_")[:200]
    return f"users/{user_id}/{safe_folder}/{file_id}_{safe_name}".replace("//", "/")


async def upload_bytes(
    *,
    user_id: UUID,
    folder: str,
    name: str,
    content: bytes,
    mime_type: str,
) -> tuple[str, UUID]:
    """Upload raw bytes; returns (r2_key, generated_file_id)."""
    file_id = uuid4()
    key = _build_key(user_id, folder, file_id, name)

    def _do() -> None:
        _client().put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=content,
            ContentType=mime_type,
            Metadata={"user_id": str(user_id), "name": name},
        )

    await asyncio.get_running_loop().run_in_executor(_executor, _do)
    return key, file_id


async def signed_url(key: str, expires_in: int = 3600) -> str:
    """Generate a time-limited signed GET URL."""

    def _do() -> str:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    return await asyncio.get_running_loop().run_in_executor(_executor, _do)


async def delete_key(key: str) -> None:
    def _do() -> None:
        _client().delete_object(Bucket=settings.r2_bucket, Key=key)

    await asyncio.get_running_loop().run_in_executor(_executor, _do)


async def delete_user_prefix(user_id: UUID) -> None:
    """Delete every object under users/{user_id}/. Used by privacy delete-my-data."""

    def _do() -> None:
        cli = _client()
        prefix = f"users/{user_id}/"
        paginator = cli.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.r2_bucket, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                cli.delete_objects(Bucket=settings.r2_bucket, Delete={"Objects": keys})

    await asyncio.get_running_loop().run_in_executor(_executor, _do)
