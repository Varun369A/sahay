"""Tests for the split-block system prompt cache structure.

The split is the difference between a Haiku request that re-reads ~3.5KB
of system tokens on every persona change vs. one that re-reads ~500B.
Lock down the structural invariants so a future refactor can't silently
collapse the blocks back into one.
"""

from __future__ import annotations

import string

from sahay.services.agent_engine import (
    _MOOD_BLOCKS,
    _ROLE_BLOCKS,
    _SYSTEM_PERSONA_TEMPLATE,
    _SYSTEM_STATIC,
    _personalized_system_blocks,
)


def test_static_block_has_no_template_variables() -> None:
    """The whole point of the static block is it doesn't depend on the
    user. Any `{...}` placeholder leaking in would per-user-fragment the
    cache and silently undo the win.
    """
    formatter = string.Formatter()
    field_names = [
        f for _, f, _, _ in formatter.parse(_SYSTEM_STATIC) if f
    ]
    assert field_names == [], (
        f"Static block has leaked template variables: {field_names}"
    )


def test_persona_template_uses_exactly_three_variables() -> None:
    """Persona must reference {name}, {role_block}, {mood_block} — and
    nothing else. A new variable introduced here without updating the
    builder would crash at runtime."""
    formatter = string.Formatter()
    fields = {
        f for _, f, _, _ in formatter.parse(_SYSTEM_PERSONA_TEMPLATE) if f
    }
    assert fields == {"name", "role_block", "mood_block"}


def test_blocks_returns_two_cache_controlled_text_blocks() -> None:
    blocks = _personalized_system_blocks("Sahay", role="engineer", mood="default")
    assert len(blocks) == 2
    for b in blocks:
        assert b["type"] == "text"
        assert b["cache_control"] == {"type": "ephemeral"}
        assert isinstance(b["text"], str) and b["text"].strip()


def test_static_block_is_first_for_prefix_caching() -> None:
    """Anthropic prompt caching is prefix-based: the cache key for
    breakpoint N is the prefix UP TO that breakpoint. For the static
    block to be shared across all users, it MUST come first — otherwise
    its cache key is contaminated by the per-user persona prefix."""
    blocks = _personalized_system_blocks("Sahay", role="engineer", mood="warm")
    assert blocks[0]["text"] == _SYSTEM_STATIC


def test_persona_block_changes_with_inputs() -> None:
    """Sanity: same user inputs → same persona block; different inputs
    → different block. The cache key is the block content, so this is
    what makes per-user-config caching work."""
    a = _personalized_system_blocks("Sahay", role="engineer", mood="default")
    b = _personalized_system_blocks("Sahay", role="engineer", mood="default")
    assert a[1]["text"] == b[1]["text"]

    c = _personalized_system_blocks("Sahay", role="engineer", mood="warm")
    assert a[1]["text"] != c[1]["text"]

    d = _personalized_system_blocks("Sahay", role="founder", mood="default")
    assert a[1]["text"] != d[1]["text"]

    e = _personalized_system_blocks("Jarvis", role="engineer", mood="default")
    assert a[1]["text"] != e[1]["text"]


def test_static_block_is_substantially_larger_than_persona() -> None:
    """The whole motivation: static is the BIG block. If they ever
    flip (e.g. someone moves rules into persona) the cache savings
    invert and we should fail loudly."""
    persona = _personalized_system_blocks("Sahay", role="engineer", mood="default")[1]["text"]
    assert len(_SYSTEM_STATIC) > 3 * len(persona), (
        f"Static block ({len(_SYSTEM_STATIC)}B) should be much larger than "
        f"persona ({len(persona)}B) for cache split to be worthwhile."
    )


def test_unknown_role_falls_back_to_other() -> None:
    blocks = _personalized_system_blocks("Sahay", role="not-a-real-role", mood="default")
    assert _ROLE_BLOCKS["other"] in blocks[1]["text"]


def test_unknown_mood_falls_back_to_default() -> None:
    blocks = _personalized_system_blocks("Sahay", role="engineer", mood="not-a-real-mood")
    assert _MOOD_BLOCKS["default"] in blocks[1]["text"]


def test_persona_contains_assistant_name() -> None:
    blocks = _personalized_system_blocks("MyHelper", role="engineer", mood="default")
    assert "MyHelper" in blocks[1]["text"]
    # And NOT in the static block — that would re-fragment the cache.
    assert "MyHelper" not in blocks[0]["text"]
