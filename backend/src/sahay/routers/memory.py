"""User memory CRUD endpoints.

Users have the right to see, edit, and delete everything the agent has
remembered about them. This router exposes that surface.

Paths:
  GET    /v1/memory            — list all the user's memories
  POST   /v1/memory             — upsert a memory (user-sourced)
  DELETE /v1/memory/{key}       — forget a memory
  POST   /v1/memory/wipe        — delete ALL memories (one-click nuke)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete as sqldelete
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User, UserMemory
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.services import memory_service
from sahay.services.rate_limiter import rate_limit

router = APIRouter(prefix="/v1/memory", tags=["memory"])


# ─── Schemas ─────────────────────────────────────────────────────────


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    value: str
    category: str
    importance: int
    last_accessed_at: datetime
    source: str


class MemoryUpsertRequest(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="facts", max_length=32)
    importance: int = Field(default=5, ge=1, le=10)


class WipeResponse(BaseModel):
    deleted: int


# ─── Endpoints ───────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[MemoryOut],
    dependencies=[Depends(rate_limit("memory_read", per_minute=60))],
)
async def list_memory(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> list[MemoryOut]:
    memories = await memory_service.list_all(db, user_id=user.id)
    return [MemoryOut.model_validate(m) for m in memories]


@router.post(
    "",
    response_model=MemoryOut,
    dependencies=[Depends(rate_limit("memory_write", per_minute=60))],
)
async def upsert_memory(
    body: MemoryUpsertRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> MemoryOut:
    m = await memory_service.remember(
        db,
        user_id=user.id,
        key=body.key,
        value=body.value,
        category=body.category,
        importance=body.importance,
        source="user",                    # explicit user-driven edit
    )
    return MemoryOut.model_validate(m)


@router.delete(
    "/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("memory_write", per_minute=60))],
)
async def delete_memory(
    key: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> None:
    removed = await memory_service.forget(db, user_id=user.id, key=key)
    if not removed:
        raise HTTPException(status_code=404, detail="memory_not_found")


@router.post(
    "/wipe",
    response_model=WipeResponse,
    dependencies=[Depends(rate_limit("memory_wipe", per_minute=2))],
)
async def wipe_memory(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> WipeResponse:
    """Nuke everything the agent has stored about this user."""
    res = await db.execute(
        sqldelete(UserMemory).where(UserMemory.user_id == user.id)
    )
    return WipeResponse(deleted=res.rowcount or 0)
