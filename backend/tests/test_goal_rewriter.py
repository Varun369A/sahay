"""Tests for the goal_rewriter pure parsing + skip logic.

The Haiku-driven `rewrite_goal()` requires a real Anthropic call;
integration tests cover that. Here we lock down the parser (parses
the response shape we expect, falls back gracefully on garbage) and
the `_looks_clean` skip heuristic (cheap path that avoids the LLM
entirely on trivially-clean inputs).
"""

from __future__ import annotations

import pytest

from sahay.services.goal_rewriter import _looks_clean, _parse_response


# ─── _looks_clean: skip the LLM ─────────────────────────────────────


def test_short_clean_text_skips_llm() -> None:
    assert _looks_clean("hello") is True
    assert _looks_clean("call rahul") is True
    assert _looks_clean("home") is True


def test_text_with_punctuation_still_clean() -> None:
    assert _looks_clean("send it,") is True
    assert _looks_clean("ok!") is True


def test_long_text_does_not_skip() -> None:
    """Anything past the threshold goes to the rewriter — long sentences
    are exactly where STT errors tend to compound."""
    assert _looks_clean("send rahool a message right now") is False


def test_non_ascii_does_not_skip() -> None:
    """Tamil / Hindi / code-mixed always goes through the rewriter,
    even when short."""
    assert _looks_clean("வணக்கம்") is False


def test_just_over_threshold_does_not_skip() -> None:
    """13 chars sits just past the 12-char skip floor — must rewrite."""
    assert _looks_clean("namaste rahul") is False
    # And one char shorter does skip:
    assert _looks_clean("namaste raju") is True


def test_empty_string_does_not_skip() -> None:
    """Empty input goes through the rewriter (which has its own
    early-exit branch returning the original empty string). Keeps the
    skip path conservative — only confidently-short ASCII clears it."""
    assert _looks_clean("") is False


# ─── _parse_response: tolerant JSON parsing ─────────────────────────


def test_parse_clean_json() -> None:
    raw = '{"goal": "Send a message to Rahul", "confidence": 0.92}'
    result = _parse_response(raw)
    assert result is not None
    assert result == ("Send a message to Rahul", pytest.approx(0.92))


def test_parse_strips_json_fence() -> None:
    raw = '```json\n{"goal": "Call boss", "confidence": 0.8}\n```'
    result = _parse_response(raw)
    assert result is not None
    assert result == ("Call boss", pytest.approx(0.8))


def test_parse_strips_bare_fence() -> None:
    raw = '```\n{"goal": "Open Netflix", "confidence": 1.0}\n```'
    assert _parse_response(raw) == ("Open Netflix", pytest.approx(1.0))


def test_parse_invalid_json_returns_none() -> None:
    assert _parse_response("not json at all") is None
    assert _parse_response("{broken json") is None


def test_parse_missing_goal_returns_none() -> None:
    assert _parse_response('{"confidence": 0.5}') is None


def test_parse_empty_goal_returns_none() -> None:
    assert _parse_response('{"goal": "", "confidence": 1.0}') is None
    assert _parse_response('{"goal": "   ", "confidence": 1.0}') is None


def test_parse_non_string_goal_returns_none() -> None:
    assert _parse_response('{"goal": 123, "confidence": 0.5}') is None


def test_parse_missing_confidence_returns_none() -> None:
    assert _parse_response('{"goal": "Send msg"}') is None


def test_parse_non_numeric_confidence_returns_none() -> None:
    assert _parse_response('{"goal": "Send msg", "confidence": "high"}') is None


def test_parse_confidence_clamped_to_unit_interval() -> None:
    """Haiku occasionally outputs >1 or <0 confidence under stress.
    The parser clamps so downstream comparisons don't have to."""
    result = _parse_response('{"goal": "x", "confidence": 1.5}')
    assert result is not None
    assert result[1] == 1.0
    result = _parse_response('{"goal": "x", "confidence": -0.3}')
    assert result is not None
    assert result[1] == 0.0


def test_parse_top_level_array_returns_none() -> None:
    """Defensive: the schema is a JSON object, not an array."""
    assert _parse_response('[{"goal": "x", "confidence": 1}]') is None


def test_parse_strips_whitespace_around_goal() -> None:
    raw = '{"goal": "  Send to Rahul  ", "confidence": 0.9}'
    result = _parse_response(raw)
    assert result is not None
    assert result[0] == "Send to Rahul"
