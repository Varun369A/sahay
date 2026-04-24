"""Document creation endpoints.

POST /v1/documents  — user-initiated doc creation (UI button or agent).

The agent also produces documents via its `create_document` tool which
runs the same service inside `agent_engine.step()`; this endpoint is the
external / UI-triggered surface.

Rate limit is low on purpose: each doc burns a Sonnet call (~2–4 paise
in Claude tokens + ~0 storage). Free users get a handful/day via the
agent; this direct-POST surface is mainly for power workflows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.schemas.document import CreateDocumentRequest, CreateDocumentResponse
from sahay.services import document_generator
from sahay.services.rate_limiter import rate_limit

router = APIRouter(prefix="/v1/documents", tags=["documents"])


@router.post(
    "",
    response_model=CreateDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("doc_create", per_minute=6))],
)
async def create_document(
    body: CreateDocumentRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> CreateDocumentResponse:
    try:
        doc = await document_generator.create(
            db=db,
            user_id=user.id,
            kind=body.kind,
            prompt=body.prompt,
            title=body.title,
            role=user.role,
        )
    except document_generator.GenerationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return CreateDocumentResponse(
        file_id=doc.file_id,
        name=doc.name,
        mime_type=doc.mime_type,
        signed_url=doc.signed_url,
        kind=doc.kind,
        format=doc.doc_format,
        tokens_used=doc.tokens_used,
        cost_inr_paise=doc.cost_inr_paise,
    )
