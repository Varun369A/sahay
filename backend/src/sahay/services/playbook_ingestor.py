"""Playbook RAG ingestion — reads an app's official user guide and turns it
into a structured action playbook (steps → screens → actions).

This is THE differentiator vs Anthropic Computer Use / Adept / Manus etc.
They learn through trial-and-error vision; we read the manual.

Flow:
  1. Fetch doc URL(s) → strip to clean text
  2. Build Claude Opus prompt with the doc + an intent (e.g. "send_message")
  3. Get back JSON: ordered steps with target_screen, action, params, fallback
  4. Validate with Pydantic, persist to `playbooks` table

Each playbook is shared across users (no PII), so cost amortises:
ingest once → millions of users execute.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import Playbook
from sahay.domain.payment_blocklist import is_payment_app
from sahay.services.claude_client import complete
from sahay.util.logging import get_logger

log = get_logger(__name__)


# ─── Schema ──────────────────────────────────────────────────────────


class PlaybookStep(BaseModel):
    """One executable step in the playbook."""

    step: int = Field(ge=1)
    description: str = Field(max_length=200)
    target_screen: str = Field(max_length=100)
    action: str = Field(pattern=r"^(launch|tap|long_press|type|swipe|wait|read|verify|back|home)$")
    target_selector: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    confirm_required: bool = False
    timeout_ms: int = Field(default=5000, ge=100, le=30_000)
    on_failure: str | None = Field(default=None, max_length=64)


class PlaybookSpec(BaseModel):
    """Full playbook spec — what we persist to `steps_json`."""

    app_package: str
    app_name: str
    intent: str
    inputs: list[str] = Field(default_factory=list)  # e.g. ["recipient_name", "message_text"]
    steps: list[PlaybookStep]
    notes: str | None = None

    @field_validator("steps")
    @classmethod
    def _steps_ordered(cls, v: list[PlaybookStep]) -> list[PlaybookStep]:
        for i, s in enumerate(v, start=1):
            if s.step != i:
                raise ValueError(f"step numbers must be 1..N consecutive (got {s.step} at {i})")
        return v


# ─── Doc fetching ────────────────────────────────────────────────────


async def _fetch_doc(url: str) -> str:
    """Fetch a URL and return cleaned text. Tolerates docs of any size up to ~200 KB."""
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"user-agent": "SahayPlaybookIngestor/0.1"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text

    # Strip scripts/styles + collapse whitespace.
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200_000]


# ─── Prompt ──────────────────────────────────────────────────────────


_SYSTEM = """You are Sahay's Playbook Generator. You read the official user guide for an Android app and produce a JSON playbook that an accessibility-service-based agent can execute step by step.

Hard rules:
1. Output ONLY valid JSON matching the schema below. No prose, no markdown fences.
2. Steps are ordered, 1..N. Each step does exactly one thing.
3. Use accessibility-tree selectors (text, contentDescription, viewId) — never pixel coordinates.
4. Mark `confirm_required: true` for any action that sends, deletes, transfers, or otherwise has external side effects.
5. Refuse to produce playbooks for payment, banking, or money-transfer flows. Return {"error": "payment_blocked"} instead.
6. Inputs are the variables the user supplies at runtime (e.g. recipient_name, message_text). List them in `inputs`.
7. Include a sensible `on_failure` for steps where common failures have known recovery (e.g. "retry_once", "ask_user", "abort").

Schema (PlaybookSpec):
{
  "app_package": "string",
  "app_name": "string",
  "intent": "snake_case_string",
  "inputs": ["input_name_1", ...],
  "steps": [
    {
      "step": 1,
      "description": "human-readable",
      "target_screen": "screen_name",
      "action": "launch|tap|long_press|type|swipe|wait|read|verify|back|home",
      "target_selector": {"text": "...", "viewId": "...", "contentDescription": "..."},
      "params": {"text": "{{input_name}}"},
      "confirm_required": false,
      "timeout_ms": 5000,
      "on_failure": null
    }
  ],
  "notes": "optional caveats"
}
"""


_USER_TEMPLATE = """App: {app_name} ({app_package})
Intent to support: {intent}

Below is the official user guide content. Generate the playbook.

--- BEGIN DOC ---
{doc}
--- END DOC ---
"""


# ─── Public API ──────────────────────────────────────────────────────


async def ingest_playbook(
    db: AsyncSession,
    *,
    app_package: str,
    app_name: str,
    intent: str,
    doc_url: str,
    app_version: str | None = None,
) -> Playbook:
    """Ingest one (app, intent) pair into a playbook row.

    Reuses existing row if (app_package, intent, app_version) already present.
    """
    if is_payment_app(app_package):
        raise ValueError(f"Refused: {app_package} is on the payment blocklist.")

    existing = await _existing(db, app_package, intent, app_version)
    if existing is not None:
        log.info("playbook_already_exists", app_package=app_package, intent=intent)
        return existing

    doc = await _fetch_doc(doc_url)
    user_msg = _USER_TEMPLATE.format(
        app_name=app_name, app_package=app_package, intent=intent, doc=doc
    )

    result = await complete(
        workload="ingestion",   # Opus — quality matters, ingestion is one-time
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=4096,
        temperature=0.0,
    )

    spec = _parse_and_validate(result.content, app_package, app_name, intent)

    pb = Playbook(
        app_package=app_package,
        app_name=app_name,
        app_version=app_version,
        intent=intent,
        steps_json=spec.model_dump(),
        source_doc_url=doc_url,
        reliability_score=0.0,   # starts unproven; update after live runs
        is_payment_app=False,
    )
    db.add(pb)
    await db.flush()
    log.info(
        "playbook_ingested",
        app_package=app_package, intent=intent, steps=len(spec.steps),
        cost_paise=result.cost_inr_paise,
    )
    return pb


def _parse_and_validate(
    raw: str, app_package: str, app_name: str, intent: str
) -> PlaybookSpec:
    raw = raw.strip()
    # Tolerate ```json fences if model added them despite instructions.
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"playbook_not_json: {e}") from e

    if isinstance(data, dict) and data.get("error") == "payment_blocked":
        raise ValueError("Model refused — payment-related intent.")

    # Force-fix the identifiers in case the model drifted.
    data["app_package"] = app_package
    data["app_name"] = app_name
    data["intent"] = intent
    return PlaybookSpec.model_validate(data)


async def _existing(
    db: AsyncSession,
    app_package: str,
    intent: str,
    app_version: str | None,
) -> Playbook | None:
    stmt = select(Playbook).where(
        Playbook.app_package == app_package,
        Playbook.intent == intent,
        Playbook.app_version == app_version,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_playbook(
    db: AsyncSession,
    app_package: str,
    intent: str,
    app_version: str | None = None,
) -> Playbook | None:
    """Lookup playbook for runtime use. Caller should fall back to vision if None."""
    if is_payment_app(app_package):
        return None
    # Try exact version match first, then any-version fallback.
    exact = await _existing(db, app_package, intent, app_version)
    if exact is not None:
        return exact
    if app_version is not None:
        return await _existing(db, app_package, intent, None)
    return None
