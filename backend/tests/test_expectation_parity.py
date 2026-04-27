"""Parity + validation tests for the step `expect` post-condition vocab."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sahay.domain.expectations import (
    EXPECT_KIND_LIST_FOR_PROMPT,
    EXPECT_KIND_REGEX,
    ExpectKind,
    Expectation,
)
from sahay.services.playbook_ingestor import PlaybookStep


_KOTLIN_FILE = (
    Path(__file__).resolve().parents[2]
    / "android"
    / "app"
    / "src"
    / "main"
    / "kotlin"
    / "io"
    / "sahay"
    / "engine"
    / "Expectation.kt"
)


def test_kind_regex_built_from_enum() -> None:
    assert EXPECT_KIND_REGEX == "^(" + "|".join(k.value for k in ExpectKind) + ")$"


def test_kind_list_for_prompt_matches_enum() -> None:
    assert EXPECT_KIND_LIST_FOR_PROMPT == "|".join(k.value for k in ExpectKind)


def test_screen_contains_requires_text() -> None:
    Expectation.model_validate({"kind": "screen_contains", "text": "Sent"})
    with pytest.raises(ValidationError):
        Expectation.model_validate({"kind": "screen_contains"})


def test_node_appears_requires_selector() -> None:
    Expectation.model_validate({"kind": "node_appears", "selector": {"text": "OK"}})
    with pytest.raises(ValidationError):
        Expectation.model_validate({"kind": "node_appears"})


def test_field_value_requires_both() -> None:
    Expectation.model_validate(
        {"kind": "field_value", "selector": {"viewId": "msg"}, "text": "hi"}
    )
    for missing in (
        {"kind": "field_value", "selector": {"viewId": "msg"}},
        {"kind": "field_value", "text": "hi"},
    ):
        with pytest.raises(ValidationError):
            Expectation.model_validate(missing)


def test_screen_changed_has_no_required_fields() -> None:
    Expectation.model_validate({"kind": "screen_changed"})


def test_unknown_kind_rejected() -> None:
    for bad in ("ScreenContains", "node_visible", "exists", ""):
        with pytest.raises(ValidationError):
            Expectation.model_validate({"kind": bad})


def test_timeout_clamped_in_schema() -> None:
    with pytest.raises(ValidationError):
        Expectation.model_validate({"kind": "screen_changed", "timeout_ms": 50})
    with pytest.raises(ValidationError):
        Expectation.model_validate({"kind": "screen_changed", "timeout_ms": 50_000})


def test_playbook_step_accepts_expect() -> None:
    PlaybookStep.model_validate(
        {
            "step": 1,
            "description": "tap send",
            "target_screen": "compose",
            "action": "tap",
            "target_selector": {"text": "Send"},
            "params": {},
            "confirm_required": True,
            "timeout_ms": 5000,
            "expect": {
                "kind": "node_disappears",
                "selector": {"text": "Send"},
                "timeout_ms": 3000,
            },
        }
    )


def test_playbook_step_expect_is_optional() -> None:
    """Backwards-compat: existing playbooks without `expect` still validate."""
    PlaybookStep.model_validate(
        {
            "step": 1,
            "description": "tap send",
            "target_screen": "compose",
            "action": "tap",
            "target_selector": {"text": "Send"},
            "params": {},
            "confirm_required": True,
            "timeout_ms": 5000,
        }
    )


def test_kotlin_mirror_has_same_kinds() -> None:
    if not _KOTLIN_FILE.exists():
        pytest.skip(f"Kotlin mirror not present at {_KOTLIN_FILE}")
    src = _KOTLIN_FILE.read_text(encoding="utf-8")
    # Match ONLY enum-value declarations like `SCREEN_CONTAINS("screen_contains")`
    # — uppercase Kotlin enum identifier, then the wire name. This skips
    # annotations like `@SerialName("timeout_ms")`.
    kotlin_kinds = set(re.findall(r'\b[A-Z][A-Z_]+\("([a-z_]+)"\)', src))
    python_kinds = {k.value for k in ExpectKind}
    assert kotlin_kinds == python_kinds, (
        f"Expectation vocab drift.\n"
        f"  In Python only: {sorted(python_kinds - kotlin_kinds)}\n"
        f"  In Kotlin only: {sorted(kotlin_kinds - python_kinds)}\n"
    )
