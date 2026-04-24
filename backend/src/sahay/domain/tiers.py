"""Subscription tiers — single source of truth for pricing and limits.

Used by: usage metering, action planner, billing UI, frontend display.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Tier(StrEnum):
    FREE = "free"
    PRO = "pro"
    POWER = "power"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class TierLimits:
    """Hard limits enforced server-side per tier."""

    daily_actions: int
    weekly_actions: int
    max_apps: int                # 0 = unlimited
    multilingual_voice: bool
    voice_clone: bool
    monthly_image_gens: int
    price_inr_paise: int         # monthly price in paise (₹1 = 100 paise)
    overage_per_action_paise: int  # cost per action above limit; 0 = no overage allowed


# Pricing in INR. Update with care — these power Razorpay subscription plans.
TIERS: dict[Tier, TierLimits] = {
    Tier.FREE: TierLimits(
        # Agent mode burns 10-20 steps per task (one Claude call each),
        # so "50 actions/day" = ~3 tasks. Bumped to 500/day so a free
        # user gets meaningful daily usage (~25-50 full tasks).
        daily_actions=500,
        weekly_actions=2_000,
        max_apps=5,
        multilingual_voice=False,
        voice_clone=False,
        monthly_image_gens=5,
        price_inr_paise=0,
        overage_per_action_paise=0,         # free tier hard-stops at limit
    ),
    Tier.PRO: TierLimits(
        daily_actions=500,
        weekly_actions=2_500,
        max_apps=0,                         # unlimited
        multilingual_voice=True,
        voice_clone=False,
        monthly_image_gens=100,
        price_inr_paise=29_900,             # ₹299/mo
        overage_per_action_paise=50,        # ₹0.50/action
    ),
    Tier.POWER: TierLimits(
        daily_actions=2_000,
        weekly_actions=10_000,
        max_apps=0,
        multilingual_voice=True,
        voice_clone=True,
        monthly_image_gens=500,
        price_inr_paise=99_900,             # ₹999/mo
        overage_per_action_paise=30,
    ),
    Tier.ENTERPRISE: TierLimits(
        daily_actions=20_000,
        weekly_actions=100_000,
        max_apps=0,
        multilingual_voice=True,
        voice_clone=True,
        monthly_image_gens=5_000,
        price_inr_paise=0,                  # negotiated
        overage_per_action_paise=20,
    ),
}


def get_limits(tier: Tier | str) -> TierLimits:
    """Lookup limits by tier. Defaults to FREE on unknown."""
    if isinstance(tier, str):
        try:
            tier = Tier(tier)
        except ValueError:
            tier = Tier.FREE
    return TIERS[tier]
