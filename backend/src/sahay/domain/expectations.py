"""Step-level expectation (post-condition) vocabulary.

Every playbook step CAN declare an `expect` predicate. After the action
runs, the device polls this predicate up to `timeout_ms`; if it never
becomes true the step is marked `expect_failed` instead of silently
passing because the action call itself didn't error.

This is the core of the verification layer — without it, "the tap
returned true" is conflated with "the tap did the right thing".

Five predicate kinds, each with the fields it actually uses:

  • SCREEN_CONTAINS    — text appears anywhere on screen
  • NODE_APPEARS       — a node matching `selector` shows up
  • NODE_DISAPPEARS    — a node matching `selector` goes away
                         (e.g. the "Send" button after a message is sent)
  • SCREEN_CHANGED     — the screen-state hash differs from before-action
  • FIELD_VALUE        — the input field at `selector` actually contains
                         `text` (catches IME swallowing or stale focus)

Kotlin mirror: `android/app/src/main/kotlin/io/sahay/engine/Expectation.kt`
Parity is enforced by `tests/test_expectation_parity.py`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ExpectKind(str, Enum):
    SCREEN_CONTAINS  = "screen_contains"
    NODE_APPEARS     = "node_appears"
    NODE_DISAPPEARS  = "node_disappears"
    SCREEN_CHANGED   = "screen_changed"
    FIELD_VALUE      = "field_value"


# Wire pattern enforced on `kind` so a typo produces a validation error
# during ingestion instead of a silent verification skip at runtime.
EXPECT_KIND_REGEX: str = r"^(" + "|".join(k.value for k in ExpectKind) + r")$"

# Stable ordered list for prompts — Opus references this when generating
# expectations.
EXPECT_KIND_LIST_FOR_PROMPT: str = "|".join(k.value for k in ExpectKind)


class Expectation(BaseModel):
    """Post-condition the runtime polls after a step's action completes."""

    kind: str = Field(pattern=EXPECT_KIND_REGEX)
    text: str | None = Field(default=None, max_length=200)
    selector: dict[str, Any] | None = None
    timeout_ms: int = Field(default=3000, ge=200, le=15_000)

    @model_validator(mode="after")
    def _required_fields_per_kind(self) -> "Expectation":
        k = self.kind
        if k == ExpectKind.SCREEN_CONTAINS.value:
            if not self.text:
                raise ValueError("screen_contains requires `text`")
        elif k in (ExpectKind.NODE_APPEARS.value, ExpectKind.NODE_DISAPPEARS.value):
            if not self.selector:
                raise ValueError(f"{k} requires `selector`")
        elif k == ExpectKind.FIELD_VALUE.value:
            if not self.selector or self.text is None:
                raise ValueError("field_value requires both `selector` and `text`")
        # screen_changed has no required fields — runtime compares before/after hash.
        return self
