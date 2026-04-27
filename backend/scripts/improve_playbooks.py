"""Cron entry-point: run the Opus-driven playbook improver against all
candidates in the library.

Usage:
    python -m scripts.improve_playbooks                # default knobs
    python -m scripts.improve_playbooks --limit 5
    python -m scripts.improve_playbooks --score 0.6    # patch even less-broken ones
    python -m scripts.improve_playbooks --dry-run      # show candidates, do nothing

Designed to run nightly via the host scheduler. Idempotent — playbooks
with an existing in-flight canary are skipped automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sahay.db.session import session_scope
from sahay.services.playbook_improver import (
    SCORE_THRESHOLD,
    find_candidates,
    improve_playbook,
)


async def main_async(args: argparse.Namespace) -> int:
    async with session_scope() as db:
        candidates = await find_candidates(
            db,
            score_threshold=args.score,
            limit=args.limit,
        )
        print(
            f"Found {len(candidates)} candidate playbook(s) "
            f"below score={args.score:.2f}.",
            flush=True,
        )

        if args.dry_run:
            for pb in candidates:
                print(
                    f"  [dry] {pb.app_package}:{pb.intent} "
                    f"score={float(pb.reliability_score):.3f} "
                    f"success={pb.success_count} fail={pb.fail_count}",
                )
            return 0

        improved = 0
        skipped = 0
        total_cost = 0
        for pb in candidates:
            print(
                f"  → improving {pb.app_package}:{pb.intent} "
                f"(score={float(pb.reliability_score):.3f})",
                flush=True,
            )
            result = await improve_playbook(db, playbook_id=pb.id)
            total_cost += result.cost_inr_paise
            if result.revision_id is not None:
                improved += 1
                print(f"     ✓ canary revision {result.revision_id}")
            else:
                skipped += 1
                print(f"     - skipped: {result.skipped_reason}")
            # Each playbook gets its own commit so a later failure
            # doesn't lose earlier wins. session_scope commits at exit;
            # we flush each step so partial progress is visible.
            await db.flush()

        print(
            f"\nDone. improved={improved} skipped={skipped} "
            f"cost_inr_paise={total_cost} (~₹{total_cost / 100:.2f})",
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--score", type=float, default=SCORE_THRESHOLD,
        help=f"Score below which a playbook is a candidate (default {SCORE_THRESHOLD}).",
    )
    parser.add_argument(
        "--limit", type=int, default=20,
        help="Max candidates to process in one run (default 20).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List candidates without calling Opus.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
