"""Parity tests for the canonical playbook action vocabulary.

Locks down two invariants:

  1. The Pydantic schema only accepts wire names listed in
     `PlaybookAction` — adding or renaming a value here propagates to
     validation automatically.
  2. The Kotlin mirror at
     `android/app/src/main/kotlin/io/sahay/engine/PlaybookAction.kt`
     contains the exact same set of wire names. If the two ever drift
     this test fails loudly instead of producing silent runtime
     `unknown_action` rejections in the field.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sahay.domain.playbook_actions import (
    ACTION_LIST_FOR_PROMPT,
    ACTION_REGEX,
    PlaybookAction,
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
    / "PlaybookAction.kt"
)


def test_regex_built_from_enum() -> None:
    expected = "^(" + "|".join(a.value for a in PlaybookAction) + ")$"
    assert ACTION_REGEX == expected


def test_prompt_string_matches_enum() -> None:
    assert ACTION_LIST_FOR_PROMPT == "|".join(a.value for a in PlaybookAction)


@pytest.mark.parametrize("action", [a.value for a in PlaybookAction])
def test_pydantic_accepts_each_canonical_action(action: str) -> None:
    PlaybookStep.model_validate(
        {
            "step": 1,
            "description": "x",
            "target_screen": "s",
            "action": action,
            "target_selector": {},
            "params": {},
            "confirm_required": False,
            "timeout_ms": 1000,
        }
    )


def test_pydantic_rejects_non_canonical_action() -> None:
    for bad in ("Tap", "open_app", "click", "scroll", ""):
        with pytest.raises(ValidationError):
            PlaybookStep.model_validate(
                {
                    "step": 1,
                    "description": "x",
                    "target_screen": "s",
                    "action": bad,
                    "target_selector": {},
                    "params": {},
                    "confirm_required": False,
                    "timeout_ms": 1000,
                }
            )


def test_kotlin_mirror_has_same_wire_names() -> None:
    """The Kotlin file declares each enum value with `wireName = "<name>"`.
    Extract those, compare to the Python enum.
    """
    if not _KOTLIN_FILE.exists():
        pytest.skip(f"Kotlin mirror not present at {_KOTLIN_FILE}")
    src = _KOTLIN_FILE.read_text(encoding="utf-8")
    # Only match enum-value declarations: `LAUNCH("launch")` etc.
    kotlin_names = set(re.findall(r'\b[A-Z][A-Z_]+\("([a-z_]+)"\)', src))
    python_names = {a.value for a in PlaybookAction}
    assert kotlin_names == python_names, (
        f"Vocab drift detected.\n"
        f"  In Python only: {sorted(python_names - kotlin_names)}\n"
        f"  In Kotlin only: {sorted(kotlin_names - python_names)}\n"
    )
