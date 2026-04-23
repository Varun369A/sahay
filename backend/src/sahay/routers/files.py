"""Files: upload, list, generate-image, edit-image."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import File as FileModel
from sahay.db.models import User
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.domain.tiers import get_limits
from sahay.schemas.file import EditImageRequest, FileOut, GenerateImageRequest
from sahay.services import r2_client, replicate_client, usage_meter
from sahay.services.rate_limiter import rate_limit
from sahay.util.logging import get_logger

router = APIRouter(prefix="/v1/files", tags=["files"])
log = get_logger(__name__)

# Hard cap to protect bucket cost. Image gens use a smaller limit via tier.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
_ALLOWED_MIME_PREFIXES = ("image/", "audio/", "video/", "application/pdf", "text/")


@router.post(
    "/upload",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("files_upload", per_minute=30))],
)
async def upload(
    file: UploadFile = File(...),
    folder: str = Form(default="/"),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> FileOut:
    if file.content_type is None or not any(
        file.content_type.startswith(p) for p in _ALLOWED_MIME_PREFIXES
    ):
        raise HTTPException(415, "unsupported_media_type")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file_too_large")

    key, file_id = await r2_client.upload_bytes(
        user_id=user.id,
        folder=folder,
        name=file.filename or "untitled",
        content=content,
        mime_type=file.content_type,
    )

    f = FileModel(
        id=file_id,
        user_id=user.id,
        folder=folder,
        name=file.filename or "untitled",
        mime_type=file.content_type,
        size_bytes=len(content),
        r2_key=key,
        source="upload",
    )
    db.add(f)
    await db.flush()

    out = FileOut.model_validate(f)
    out.download_url = await r2_client.signed_url(key)
    return out


@router.post(
    "/generate-image",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("image_gen", per_minute=10))],
)
async def generate_image(
    body: GenerateImageRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> FileOut:
    # Image-gen tier limit (separate from action count).
    snap = await usage_meter.get_snapshot(db, user.id, user.tier)
    limit = get_limits(user.tier).monthly_image_gens
    if snap.monthly_image_gens_used >= limit:
        raise HTTPException(429, "monthly_image_limit_reached")

    image_bytes, mime = await replicate_client.generate_image(
        body.prompt, aspect_ratio=body.aspect_ratio, seed=body.seed,
    )

    name = body.name or "generated.png"
    if not name.lower().endswith(".png"):
        name = f"{name}.png"

    key, file_id = await r2_client.upload_bytes(
        user_id=user.id,
        folder=body.folder,
        name=name,
        content=image_bytes,
        mime_type=mime,
    )

    f = FileModel(
        id=file_id,
        user_id=user.id,
        folder=body.folder,
        name=name,
        mime_type=mime,
        size_bytes=len(image_bytes),
        r2_key=key,
        source="generated",
    )
    db.add(f)
    await usage_meter.increment_action(db, user.id, is_image_gen=True)
    await db.flush()

    out = FileOut.model_validate(f)
    out.download_url = await r2_client.signed_url(key)
    return out


@router.patch(
    "/{file_id}/edit",
    response_model=FileOut,
    dependencies=[Depends(rate_limit("image_gen", per_minute=10))],
)
async def edit_image(
    file_id: UUID,
    body: EditImageRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> FileOut:
    parent = await db.get(FileModel, file_id)
    if parent is None or parent.user_id != user.id:
        raise HTTPException(404, "file_not_found")
    if not parent.mime_type.startswith("image/"):
        raise HTTPException(400, "not_an_image")

    snap = await usage_meter.get_snapshot(db, user.id, user.tier)
    limit = get_limits(user.tier).monthly_image_gens
    if snap.monthly_image_gens_used >= limit:
        raise HTTPException(429, "monthly_image_limit_reached")

    base_url = await r2_client.signed_url(parent.r2_key, expires_in=600)
    image_bytes, mime = await replicate_client.edit_image(base_url, body.prompt)

    new_name = f"edit_{parent.name}"
    key, new_file_id = await r2_client.upload_bytes(
        user_id=user.id,
        folder=parent.folder,
        name=new_name,
        content=image_bytes,
        mime_type=mime,
    )

    f = FileModel(
        id=new_file_id,
        user_id=user.id,
        folder=parent.folder,
        name=new_name,
        mime_type=mime,
        size_bytes=len(image_bytes),
        r2_key=key,
        parent_id=parent.id,
        source="edited",
    )
    db.add(f)
    await usage_meter.increment_action(db, user.id, is_image_gen=True)
    await db.flush()

    out = FileOut.model_validate(f)
    out.download_url = await r2_client.signed_url(key)
    return out


@router.get(
    "",
    response_model=list[FileOut],
    dependencies=[Depends(rate_limit("files_list", per_minute=60))],
)
async def list_files(
    folder: str = "/",
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> list[FileOut]:
    limit = max(1, min(limit, 200))
    stmt = (
        select(FileModel)
        .where(FileModel.user_id == user.id, FileModel.folder == folder)
        .order_by(desc(FileModel.created_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: list[FileOut] = []
    for r in rows:
        item = FileOut.model_validate(r)
        item.download_url = await r2_client.signed_url(r.r2_key)
        out.append(item)
    return out


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> None:
    f = await db.get(FileModel, file_id)
    if f is None or f.user_id != user.id:
        raise HTTPException(404, "file_not_found")
    await r2_client.delete_key(f.r2_key)
    await db.delete(f)
