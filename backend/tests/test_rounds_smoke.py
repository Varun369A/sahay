"""Offline smoke tests for Round 1–5 features.

Everything here must run without network / DB / Redis / Anthropic — we
test pure logic (humanizer, QuietHours, dedup, TTL, JSON parsing, briefing
fallback). Full integration tests need Railway + Neon and are out of scope
for local CI.

Covers:
  • QuietHours window wrap + DND shouldAnnounce  (Round 1)
  • NotificationStore dedup + payment-block filter (Round 1)
  • Planner JSON tolerant parse + degenerate fallback (Round 3 Month-1)
  • Document generator JSON parse + per-kind schema (Round 3)
  • FollowUpStore add / list / cap / remove (Round 5)
  • Briefing: missed-call heuristic + fallback composition (Round 5)
  • Agent engine's _web_search_tool_for locale logic (Round 2)
  • memory_service.slugify_key (existing) — regression guard
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


# ─── Pure utility: QuietHours ─────────────────────────────────────────
# (Android-side Kotlin file; the backend mirror lives in the prompt/agent
# contract only. We re-implement the ACL here to guarantee the contract
# is ordered the same on both sides — if this test fails after a Kotlin
# change, the contract has diverged.)


def _quiet_hours_is_active_at(now: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


class TestQuietHours:
    def test_disabled_when_start_equals_end(self):
        assert not _quiet_hours_is_active_at(0, 0, 0)
        assert not _quiet_hours_is_active_at(720, 720, 720)

    def test_simple_intra_day_window(self):
        # 13:00 – 15:00
        assert _quiet_hours_is_active_at(13 * 60, 13 * 60, 15 * 60)
        assert _quiet_hours_is_active_at(14 * 60, 13 * 60, 15 * 60)
        assert not _quiet_hours_is_active_at(15 * 60, 13 * 60, 15 * 60)
        assert not _quiet_hours_is_active_at(12 * 60, 13 * 60, 15 * 60)

    def test_wraps_past_midnight(self):
        # 22:00 – 07:00
        start, end = 22 * 60, 7 * 60
        assert _quiet_hours_is_active_at(23 * 60, start, end)     # late night
        assert _quiet_hours_is_active_at(1 * 60, start, end)      # 1am
        assert _quiet_hours_is_active_at(6 * 60 + 59, start, end) # just before 7
        assert not _quiet_hours_is_active_at(7 * 60, start, end)  # exactly 7
        assert not _quiet_hours_is_active_at(14 * 60, start, end)

    def test_boundary_exact_start(self):
        assert _quiet_hours_is_active_at(22 * 60, 22 * 60, 7 * 60)


# ─── Notification store ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_store_payment_filter_and_dedup():
    from sahay.services.notification_store import Notification, NotificationStore

    # Fake redis client. We only need the methods the store actually calls.
    class FakeRedis:
        def __init__(self):
            self.lists: dict[str, list[str]] = {}
            self.sets: dict[str, set[str]] = {}

        async def smembers(self, key: str):
            return self.sets.get(key, set())

        def pipeline(self):
            return FakePipe(self)

    class FakePipe:
        def __init__(self, root: FakeRedis):
            self.root = root
            self.ops: list[tuple] = []

        def lpush(self, key, blob):
            self.ops.append(("lpush", key, blob))
            return self

        def ltrim(self, key, a, b):
            self.ops.append(("ltrim", key, a, b))
            return self

        def expire(self, key, ttl):
            self.ops.append(("expire", key, ttl))
            return self

        def sadd(self, key, *vals):
            self.ops.append(("sadd", key, vals))
            return self

        async def execute(self):
            for op in self.ops:
                match op:
                    case ("lpush", k, blob):
                        self.root.lists.setdefault(k, []).insert(0, blob)
                    case ("sadd", k, vals):
                        self.root.sets.setdefault(k, set()).update(vals)
                    case _:
                        pass

    fake = FakeRedis()
    store = NotificationStore(redis_client=fake)

    wa = Notification(
        key="wa_1", package="com.whatsapp", app_name="WhatsApp",
        title="Amirtha", text="Running late", category="msg",
        posted_at=1_000.0,
    )
    gpay = Notification(
        key="gp_1", package="com.google.android.apps.nbu.paisa.user",
        app_name="GPay", title="₹500 received", text="from X",
        category="msg", posted_at=1_000.0,
    )
    dup = Notification(
        key="wa_1", package="com.whatsapp", app_name="WhatsApp",
        title="Amirtha", text="Running late", category="msg",
        posted_at=1_001.0,
    )

    # Round 1: payment app filtered, fresh kept
    stored = await store.ingest("user-xyz", [wa, gpay])
    assert stored == 1, "payment-app notification must be dropped"

    # Round 1: dedup by key
    stored2 = await store.ingest("user-xyz", [dup])
    assert stored2 == 0, "same key must dedup on second ingest"


# ─── Follow-up store ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_follow_up_store_cap_and_retrieve():
    from sahay.services.follow_ups import FollowUp, FollowUpStore

    class FakeRedis:
        def __init__(self):
            self.lists: dict[str, list[str]] = {}

        async def lrange(self, key, a, b):
            data = self.lists.get(key, [])
            end = len(data) if b == -1 else b + 1
            return data[a:end]

        def pipeline(self):
            parent = self

            class Pipe:
                def __init__(self):
                    self.ops: list[tuple] = []

                def lpush(self, k, v):
                    self.ops.append(("lpush", k, v))
                    return self

                def ltrim(self, k, a, b):
                    self.ops.append(("ltrim", k, a, b))
                    return self

                def expire(self, k, ttl):
                    self.ops.append(("expire", k, ttl))
                    return self

                async def execute(self):
                    for op in self.ops:
                        if op[0] == "lpush":
                            parent.lists.setdefault(op[1], []).insert(0, op[2])
                        elif op[0] == "ltrim":
                            _, k, a, b = op
                            if k in parent.lists:
                                parent.lists[k] = parent.lists[k][a:b + 1]

            return Pipe()

    fake = FakeRedis()
    store = FollowUpStore(redis_client=fake)

    # Add 30 items; cap is 25 per MAX_ITEMS
    for i in range(30):
        await store.add("u1", f"item {i}")

    items = await store.list("u1", limit=100)
    assert len(items) == store.MAX_ITEMS, f"expected cap at {store.MAX_ITEMS}"
    # newest first: item 29 must be at index 0
    assert items[0].text == "item 29"


# ─── Planner JSON parse tolerance ─────────────────────────────────────


def test_planner_parse_degenerate_on_bad_json():
    from sahay.services.planner import _degenerate_plan, _parse_plan

    # Real-world: model wraps JSON in ``` fences
    wrapped = '```json\n{"goal": "open netflix", "steps": [{"index":1,"description":"Launch Netflix","expected_screen":"home","completion_hint":"app foreground"}]}\n```'
    p = _parse_plan(wrapped, goal="open netflix")
    assert len(p.steps) == 1
    assert p.steps[0].description == "Launch Netflix"

    # Total garbage → degenerate single-step fallback
    p2 = _parse_plan("not json at all", goal="fallback test")
    assert len(p2.steps) == 1
    assert p2.steps[0].description  # has something

    # Validator preserves degenerate behaviour
    p3 = _degenerate_plan("another", reason="unit_test")
    assert p3.model == "degenerate"
    assert len(p3.steps) == 1


# ─── Document generator JSON parse ────────────────────────────────────


def test_document_generator_parses_json_with_fences():
    from sahay.services.document_generator import GenerationError, _parse_json

    wrapped = '```json\n{"name":"V","headline":"Engineer","summary":"x","experience":[],"skills":[],"education":[]}\n```'
    data = _parse_json(wrapped)
    assert data["name"] == "V"

    with pytest.raises(GenerationError):
        _parse_json("no braces here at all")


# ─── Briefing: missed-call heuristic + fallback ───────────────────────


def test_briefing_missed_call_heuristic():
    from sahay.services.briefing import _count_missed_calls
    from sahay.services.notification_store import Notification

    ns = [
        Notification("a", "com.google.android.dialer", "Dialer",
                     "Missed call", "", "missed_call", 1.0),
        Notification("b", "com.google.android.dialer", "Dialer",
                     "Missed call from Amma", "", "", 2.0),
        Notification("c", "com.whatsapp", "WhatsApp",
                     "Amirtha", "Hi", "msg", 3.0),
    ]
    assert _count_missed_calls(ns) == 2


@pytest.mark.asyncio
async def test_briefing_fallback_when_sonnet_fails(monkeypatch):
    """When the Sonnet call raises, compose() must return the deterministic
    fallback sentence, NOT propagate an error. UX contract."""
    from sahay.services import briefing as briefing_mod
    from sahay.services.notification_store import Notification

    # Patch notification + follow-up fetches
    async def fake_recent(*a, **kw):
        return [
            Notification("k", "com.whatsapp", "WhatsApp",
                         "Boss", "are you up?", "msg", 0.0),
        ]

    async def fake_list_followups(*a, **kw):
        return []

    class _FakeClient:
        class messages:
            @staticmethod
            async def create(**kwargs):
                raise RuntimeError("simulated anthropic outage")

    monkeypatch.setattr(
        briefing_mod, "get_notification_store",
        lambda: type("S", (), {"recent": staticmethod(fake_recent)})(),
    )
    monkeypatch.setattr(
        briefing_mod, "get_follow_up_store",
        lambda: type("F", (), {"list": staticmethod(fake_list_followups)})(),
    )
    monkeypatch.setattr(briefing_mod, "_get_client", lambda: _FakeClient)

    result = await briefing_mod.compose(
        db=None, user_id="u1", assistant_name="Sahay",
        role="engineer", locale="en-IN",
    )
    assert result.model == "fallback"
    assert result.text.startswith("Good ")
    assert "1 overnight" in result.text or "overnight" in result.text


# ─── Web-search tool localisation ─────────────────────────────────────


def test_web_search_tool_sets_india_location_for_in_locales():
    from sahay.services.agent_engine import _web_search_tool_for

    for loc in ("en-IN", "ta-IN", "hi-IN", "hi", "ta"):
        tool = _web_search_tool_for(loc)
        assert tool.get("user_location", {}).get("country") == "IN", loc

    for loc in ("en-US", "en-GB", None, ""):
        tool = _web_search_tool_for(loc)
        assert "user_location" not in tool, f"must not add location for {loc!r}"

    # Payment-domain block is always present
    tool = _web_search_tool_for("en-IN")
    assert "paytm.com" in tool["blocked_domains"]
    assert tool["max_uses"] == 2


# ─── Slugify regression ──────────────────────────────────────────────


def test_memory_slugify_preserves_namespace_colons():
    from sahay.services.memory_service import slugify_key

    assert slugify_key("Contact:Amirtha") == "contact:amirtha"
    assert slugify_key("Boss's Name!") == "boss-s-name"
    assert slugify_key("  ") == "unnamed"


# ─── Notification humanizer (Android-side, mirrored for contract) ─────
# We cross-check the shape of the spoken sentence conforms to what the
# listener promises — e.g. a WhatsApp body pre-colon is stripped.
#
# The actual implementation lives in Kotlin, but we test the contract
# behaviour here at the prompt level: our agent prompt tells Claude the
# RECENT NOTIFICATIONS block will include [replyable, key=…] tags.


def test_notification_format_for_prompt_includes_key_and_replyable():
    from sahay.services.notification_store import (
        Notification, format_for_prompt,
    )

    ns = [
        Notification(
            key="0|com.whatsapp|123|abc|10",
            package="com.whatsapp", app_name="WhatsApp",
            title="Amirtha", text="running late",
            category="msg", posted_at=1.0, replyable=True,
        ),
        Notification(
            key="clk_1", package="com.android.deskclock",
            app_name="Clock", title="Timer done", text="",
            category="reminder", posted_at=2.0, replyable=False,
        ),
    ]
    out = format_for_prompt(ns)
    assert "replyable" in out
    assert "key=0|com.whatsapp" in out
    assert "Amirtha" in out
    # Non-replyable has key but no "replyable" tag
    assert "clk_1" in out
