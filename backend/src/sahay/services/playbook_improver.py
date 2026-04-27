"""Nightly Opus-driven playbook improvement loop.

For each playbook whose Bayesian reliability score has dropped below a
threshold AND has accumulated enough failure samples, ask Opus 4.7 to
read the current spec + the structured failure-class aggregates and
emit a patched spec. Patches land as `PlaybookRevision` rows with
`status='canary'`; routing + promotion happen in the next task.

Why this is the compounding flywheel:
  • Verification (Task 4) makes failures observable and structured.
  • Reliability (Task 5) gives every (app, intent) a real score.
  • Failure classifier (Task 9) turns noisy reasons into actionable labels.
  • THIS module reads those signals and proposes fixes — without humans.

Cost shape: one Opus call per candidate playbook per night. Even at
10 candidates/night that's < ₹50/day. The win is irreversible — every
fix improves reliability for ALL users sharing that playbook.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import Playbook, PlaybookRevision, PlaybookRunOutcome
from sahay.domain.payment_blocklist import is_payment_app
from sahay.services.claude_client import complete
from sahay.services.playbook_ingestor import PlaybookSpec
from sahay.util.logging import get_logger

log = get_logger(__name__)


# ─── Knobs ──────────────────────────────────────────────────────────


# Below this score → eligible for re-ingestion. 0.7 matches the planner's
# trust threshold in `services/action_planner.py`, so we patch playbooks
# that have already fallen out of the trusted tier.
SCORE_THRESHOLD = 0.7

# Need at least this many failure rows before we ask Opus to fix it —
# noise floor. With fewer samples, the score may just be unlucky variance.
MIN_FAILURE_SAMPLES = 5

# Look only at recent failures so an old, since-resolved issue doesn't
# steer the patch.
LOOKBACK_DAYS = 14


@dataclass(slots=True)
class FailureBreakdown:
    """Per-(playbook, step) aggregation of failure classes."""
    total_failures: int
    by_class: dict[str, int]
    failing_step_indexes: list[int]    # most-frequent first


@dataclass(slots=True)
class ImprovementResult:
    """Outcome of one improver run on one playbook."""
    playbook_id: UUID
    revision_id: UUID | None
    skipped_reason: str | None
    cost_inr_paise: int


# ─── Candidate selection ────────────────────────────────────────────


async def find_candidates(
    db: AsyncSession,
    *,
    score_threshold: float = SCORE_THRESHOLD,
    min_failure_samples: int = MIN_FAILURE_SAMPLES,
    limit: int = 20,
) -> list[Playbook]:
    """Return playbooks worth asking Opus to improve.

    Selection criteria (all must hold):
      • not on the payment blocklist
      • reliability_score < threshold
      • fail_count >= min_failure_samples
      • does NOT already have an in-flight canary revision
    """
    stmt = (
        select(Playbook)
        .where(Playbook.is_payment_app.is_(False))
        .where(Playbook.reliability_score < score_threshold)
        .where(Playbook.fail_count >= min_failure_samples)
        .order_by(Playbook.reliability_score.asc(), Playbook.fail_count.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()

    # Drop any that already have an open canary — don't pile patches on
    # top of an unverified one.
    out: list[Playbook] = []
    for pb in rows:
        has_canary = await db.scalar(
            select(func.count(PlaybookRevision.id))
            .where(PlaybookRevision.playbook_id == pb.id)
            .where(PlaybookRevision.status == "canary")
        )
        if has_canary == 0:
            out.append(pb)
    return out


# ─── Failure aggregation ────────────────────────────────────────────


async def aggregate_failure_classes(
    db: AsyncSession,
    *,
    playbook_id: UUID,
    lookback_days: int = LOOKBACK_DAYS,
) -> FailureBreakdown:
    """Group recent outcomes for one playbook by failure_class + step."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    stmt = (
        select(
            PlaybookRunOutcome.failure_class,
            PlaybookRunOutcome.failed_step_index,
        )
        .where(PlaybookRunOutcome.playbook_id == playbook_id)
        .where(PlaybookRunOutcome.failure_class.isnot(None))
        .where(PlaybookRunOutcome.created_at >= cutoff)
    )
    rows = (await db.execute(stmt)).all()

    by_class: Counter[str] = Counter()
    by_step: Counter[int] = Counter()
    for cls, step_idx in rows:
        by_class[cls] += 1
        if step_idx is not None:
            by_step[step_idx] += 1

    return FailureBreakdown(
        total_failures=len(rows),
        by_class=dict(by_class),
        failing_step_indexes=[s for s, _ in by_step.most_common()],
    )


# ─── Prompt + parsing ───────────────────────────────────────────────


_IMPROVER_SYSTEM = """You are Sahay's Playbook Improver. You read an existing playbook and the structured failure classes that were observed in production, and you produce ONE patched playbook spec OR an explicit refusal.

Hard rules:
1. Output ONLY valid JSON. No prose, no markdown fences.
2. If you can patch it, output a complete PlaybookSpec — same schema as ingestion. Do NOT emit a partial diff; full spec only.
3. If the failures look unfixable from the doc/spec alone (e.g. permission_missing, network_error dominant), output {"status": "unfixable_by_doc", "reason": "<short reason>"}.
4. Preserve the playbook's `app_package`, `app_name`, and `intent` exactly.
5. Use the same canonical action vocab as ingestion. No invented action names.
6. Every step MUST include an `expect` post-condition. Tighten or replace existing expects where they're suspected of false-failing.
7. Use the failure-class hint to pick the right kind of fix:
   - `selector_stale`        → propose a more robust selector chain (text + cd + viewId, plus an optional `sibling_anchor` structural fallback like {"anchor": {"text": "Type a message"}, "direction": "next_clickable"} when neighbours are stable across app updates)
   - `dialog_appeared`       → insert a dismissal step before the failing one
   - `timing_issue`          → bump timeout_ms / add a wait or screen_changed expect
   - `ime_issue`             → re-focus the field before typing; stricter field_value expect
   - `gesture_rejected`      → swap tap for tap_node where possible; alternative target
   - `app_state_unexpected`  → add a recovery branch (back / home / open_app)
   - `ambiguous_match`       → make the selector more specific
   - `unknown`               → flag as unfixable_by_doc unless you see a clear cause
8. Also include a top-level `notes` field summarising what you changed and why (used in the canary review UI).
"""


def build_improvement_prompt(
    pb: Playbook,
    breakdown: FailureBreakdown,
) -> str:
    """User-message body for the improver call. Includes the current spec
    + the failure-class histogram + the failing-step indexes so Opus
    knows exactly where to focus."""
    spec_json = json.dumps(pb.steps_json, ensure_ascii=False, indent=2)
    classes = "\n".join(
        f"  • {cls}: {count}" for cls, count in
        sorted(breakdown.by_class.items(), key=lambda kv: -kv[1])
    ) or "  (none)"
    failing_steps = (
        ", ".join(str(i) for i in breakdown.failing_step_indexes[:5])
        or "(no specific step — failures spread across the playbook)"
    )
    return (
        f"Playbook: {pb.app_name} ({pb.app_package}) / intent={pb.intent}\n"
        f"Current reliability_score: {float(pb.reliability_score):.3f}\n"
        f"Successes / failures (lifetime): {pb.success_count} / {pb.fail_count}\n"
        f"Failures observed in the last {LOOKBACK_DAYS} days: {breakdown.total_failures}\n"
        f"\nFailure classes:\n{classes}\n"
        f"\nMost-failing step indexes (1-based): {failing_steps}\n"
        f"\nCurrent spec:\n```json\n{spec_json}\n```\n"
    )


def parse_improvement_response(
    raw: str,
    pb: Playbook,
) -> tuple[PlaybookSpec | None, str | None]:
    """Returns (spec, None) on a real patch, or (None, reason) on refusal.

    Tolerates ```json fences if Opus added them despite instructions.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"invalid_json: {e}"

    if isinstance(data, dict) and data.get("status") == "unfixable_by_doc":
        return None, f"unfixable: {str(data.get('reason') or '').strip()[:200]}"

    # Force-fix the identifiers — same defence the ingestor uses.
    if isinstance(data, dict):
        data["app_package"] = pb.app_package
        data["app_name"] = pb.app_name
        data["intent"] = pb.intent
    try:
        spec = PlaybookSpec.model_validate(data)
    except Exception as e:
        return None, f"validation_failed: {e!s}"
    return spec, None


# ─── Main entrypoint ────────────────────────────────────────────────


async def improve_playbook(
    db: AsyncSession,
    *,
    playbook_id: UUID,
) -> ImprovementResult:
    """Run the full improver loop for one playbook.

    On success: writes a `PlaybookRevision` row with status='canary'.
    On refusal / validation failure: logs and returns a skip reason.
    """
    pb = await db.scalar(select(Playbook).where(Playbook.id == playbook_id))
    if pb is None:
        return ImprovementResult(playbook_id, None, "playbook_not_found", 0)
    if is_payment_app(pb.app_package):
        return ImprovementResult(playbook_id, None, "payment_blocked", 0)

    breakdown = await aggregate_failure_classes(db, playbook_id=playbook_id)
    if breakdown.total_failures < MIN_FAILURE_SAMPLES:
        return ImprovementResult(
            playbook_id, None, f"insufficient_samples:{breakdown.total_failures}", 0,
        )

    user_msg = build_improvement_prompt(pb, breakdown)
    result = await complete(
        workload="ingestion",                # Opus 4.7
        system=_IMPROVER_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=4096,
        temperature=0.0,
    )
    spec, refusal = parse_improvement_response(result.content, pb)
    if spec is None:
        log.info(
            "improver_skipped",
            playbook_id=str(playbook_id),
            reason=refusal,
            cost_paise=result.cost_inr_paise,
        )
        return ImprovementResult(
            playbook_id, None, refusal or "no_spec_returned", result.cost_inr_paise,
        )

    next_version = (await _next_revision_version(db, playbook_id)) or 1
    rev = PlaybookRevision(
        playbook_id=playbook_id,
        version=next_version,
        spec_json=spec.model_dump(),
        created_by="opus_improver",
        status="canary",
        notes=spec.notes or None,
        # Snapshot the parent score so promotion math doesn't drift with
        # ongoing non-canary traffic during the canary window.
        parent_score_baseline=float(pb.reliability_score),
    )
    db.add(rev)
    await db.flush()

    log.info(
        "improver_revision_created",
        playbook_id=str(playbook_id),
        revision_id=str(rev.id),
        version=next_version,
        cost_paise=result.cost_inr_paise,
        breakdown=breakdown.by_class,
    )
    return ImprovementResult(
        playbook_id, rev.id, None, result.cost_inr_paise,
    )


async def _next_revision_version(
    db: AsyncSession, playbook_id: UUID,
) -> int | None:
    cur = await db.scalar(
        select(func.max(PlaybookRevision.version))
        .where(PlaybookRevision.playbook_id == playbook_id)
    )
    return (cur or 0) + 1


# ─── Promotion / rollback ───────────────────────────────────────────


async def promote_revision(db: AsyncSession, *, revision_id: UUID) -> bool:
    """Move a canary revision into 'promoted' and copy its spec onto the
    parent playbook's `steps_json`. Resets the parent's Bayesian counters
    so the new spec earns its own score from scratch."""
    rev = await db.scalar(
        select(PlaybookRevision).where(PlaybookRevision.id == revision_id)
    )
    if rev is None or rev.status != "canary":
        return False
    pb = await db.scalar(select(Playbook).where(Playbook.id == rev.playbook_id))
    if pb is None:
        return False

    pb.steps_json = rev.spec_json
    pb.success_count = 0
    pb.fail_count = 0
    pb.expect_fail_count = 0
    pb.reliability_score = 0.5    # Laplace prior — earn trust from scratch
    rev.status = "promoted"
    rev.promoted_at = datetime.now(tz=timezone.utc)
    await db.flush()
    log.info(
        "improver_revision_promoted",
        revision_id=str(revision_id), playbook_id=str(pb.id),
    )
    return True


async def rollback_revision(db: AsyncSession, *, revision_id: UUID) -> bool:
    """Mark a canary as rolled_back. The parent playbook's `steps_json`
    is unchanged — canary routing (next task) is the only place it would
    have been served."""
    rev = await db.scalar(
        select(PlaybookRevision).where(PlaybookRevision.id == revision_id)
    )
    if rev is None or rev.status != "canary":
        return False
    rev.status = "rolled_back"
    await db.flush()
    log.info("improver_revision_rolled_back", revision_id=str(revision_id))
    return True
