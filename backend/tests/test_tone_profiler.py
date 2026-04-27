"""Tests for the tone profiler — relationship defaults + parser logic.

The DB-touching `tone_for_contact` is covered separately in integration
tests; here we lock down the in-memory helpers (relationship defaults,
fallback descriptor, keyword list).
"""

from __future__ import annotations

from sahay.services.tone_profiler import (
    _FALLBACK_DESCRIPTOR,
    _RELATIONSHIP_DEFAULTS,
    _TONE_KEYWORDS,
)


def test_fallback_descriptor_is_safe_default() -> None:
    """Should never be empty — empty descriptor in critic prompt would
    let Haiku invent its own tone, defeating the whole feature."""
    assert _FALLBACK_DESCRIPTOR.strip()
    assert len(_FALLBACK_DESCRIPTOR) >= 30


def test_boss_default_is_formal() -> None:
    boss = _RELATIONSHIP_DEFAULTS["boss"]
    assert "formal" in boss.lower()
    assert "no emojis" in boss.lower()


def test_family_defaults_are_warm_and_casual() -> None:
    for kin in ("amma", "appa", "wife", "husband", "brother", "sister"):
        d = _RELATIONSHIP_DEFAULTS[kin].lower()
        # All family members get casual + short defaults
        assert "casual" in d or "warm" in d
        assert "short" in d


def test_amma_default_includes_code_mixed() -> None:
    """South-Indian / Indian mom-dad defaults should mention code-mixed
    Tamil/Hindi-English so Haiku doesn't produce sterile English."""
    d = _RELATIONSHIP_DEFAULTS["amma"].lower()
    assert "tamil" in d or "code-mixed" in d


def test_colleague_default_is_professional_friendly() -> None:
    d = _RELATIONSHIP_DEFAULTS["colleague"].lower()
    assert "professional" in d
    assert "friendly" in d


def test_tone_keywords_cover_common_voice_descriptors() -> None:
    """The keyword list must catch what users typically write into a
    contact's LTM value field."""
    for kw in ("formal", "casual", "short", "emojis", "tamil-english"):
        assert kw in _TONE_KEYWORDS


def test_relationship_defaults_have_sane_lengths() -> None:
    """Each default must be a single short sentence — gets injected into
    the critic prompt, so verbose strings inflate token cost."""
    for rel, default in _RELATIONSHIP_DEFAULTS.items():
        assert len(default) < 200, f"{rel} descriptor too long: {len(default)}"
        assert default.endswith("."), f"{rel} descriptor missing terminal period"
