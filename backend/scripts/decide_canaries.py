"""Cron entry-point: evaluate every in-flight canary revision and
promote / roll back / hold based on its measured Bayesian score.

Pairs with `scripts.improve_playbooks` (which CREATES canaries).
Run order in the cron schedule:
    1. improve_playbooks       — generates new canaries from failures
    2. (24h later) decide_canaries — promotes the winners, rolls back losers

Usage:
    python -m scripts.decide_canaries
    python -m scripts.decide_canaries --dry-run

Idempotent: a 'hold' verdict leaves the canary in place to be evaluated
again next run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sahay.db.session import session_scope
from sahay.services.canary_decider import _verdict_for, evaluate_all_canaries
from sqlalchemy import select


async def main_async(args: argparse.Namespace) -> int:
    async with session_scope() as db:
        if args.dry_run:
            from datetime import datetime, timezone
            from sahay.db.models import PlaybookRevision

            stmt = (
                select(PlaybookRevision)
                .where(PlaybookRevision.status == "canary")
            )
            revs = (await db.execute(stmt)).scalars().all()
            now = datetime.now(tz=timezone.utc)
            print(f"Found {len(revs)} active canary revision(s).", flush=True)
            for rev in revs:
                v = _verdict_for(rev, now=now)
                print(
                    f"  rev={str(rev.id)[:8]} "
                    f"score={v.canary_score:.3f} "
                    f"baseline={v.parent_baseline:.3f} "
                    f"runs={v.canary_total} "
                    f"→ {v.decision}  ({v.reason})",
                )
            return 0

        verdicts = await evaluate_all_canaries(db)
        promoted = sum(1 for v in verdicts if v.decision == "promote")
        rolled = sum(1 for v in verdicts if v.decision == "rollback")
        held = sum(1 for v in verdicts if v.decision == "hold")
        print(
            f"Evaluated {len(verdicts)} canary(ies). "
            f"promoted={promoted} rolled_back={rolled} held={held}",
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show verdicts without applying them.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
