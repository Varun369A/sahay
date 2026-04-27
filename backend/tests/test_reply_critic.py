"""Tests for the reply critic's response parser.

The Haiku call itself needs an API key + integration test. The parser
is the brittle part — it has to absorb fence variation, missing fields,
and string-truthy `rewritten` values without falling over.
"""

from __future__ import annotations

import json

from sahay.services.reply_critic import _parse_response


def test_parse_clean_json() -> None:
    raw = json.dumps({"text": "On it.", "rewritten": True, "reason": "shortened"})
    result = _parse_response(raw)
    assert result == ("On it.", True, "shortened")


def test_parse_strips_json_fence() -> None:
    raw = '```json\n{"text": "ok", "rewritten": false, "reason": "as-is"}\n```'
    assert _parse_response(raw) == ("ok", False, "as-is")


def test_parse_strips_bare_fence() -> None:
    raw = '```\n{"text": "yo", "rewritten": true, "reason": "casual"}\n```'
    assert _parse_response(raw) == ("yo", True, "casual")


def test_parse_invalid_json_returns_none() -> None:
    assert _parse_response("not json") is None
    assert _parse_response("{broken") is None


def test_parse_missing_text_returns_none() -> None:
    raw = json.dumps({"rewritten": True, "reason": "x"})
    assert _parse_response(raw) is None


def test_parse_non_string_text_returns_none() -> None:
    raw = json.dumps({"text": 42, "rewritten": True})
    assert _parse_response(raw) is None


def test_parse_string_rewritten_truthy() -> None:
    """Haiku occasionally returns "true"/"false" as strings under stress."""
    raw = json.dumps({"text": "ok", "rewritten": "true"})
    assert _parse_response(raw) == ("ok", True, "")
    raw = json.dumps({"text": "ok", "rewritten": "false"})
    assert _parse_response(raw) == ("ok", False, "")


def test_parse_unknown_rewritten_type_returns_none() -> None:
    raw = json.dumps({"text": "ok", "rewritten": 1})
    assert _parse_response(raw) is None


def test_parse_missing_reason_defaults_to_empty() -> None:
    raw = json.dumps({"text": "ok", "rewritten": False})
    result = _parse_response(raw)
    assert result == ("ok", False, "")


def test_parse_truncates_long_reason() -> None:
    long_reason = "x" * 500
    raw = json.dumps({"text": "ok", "rewritten": False, "reason": long_reason})
    result = _parse_response(raw)
    assert result is not None
    assert len(result[2]) <= 80


def test_parse_top_level_array_returns_none() -> None:
    """Defensive — schema is an object, not an array."""
    assert _parse_response('[{"text": "ok", "rewritten": false}]') is None


def test_parse_strips_text_whitespace() -> None:
    raw = json.dumps({"text": "  Send to Rahul  ", "rewritten": True})
    result = _parse_response(raw)
    assert result is not None
    assert result[0] == "Send to Rahul"
