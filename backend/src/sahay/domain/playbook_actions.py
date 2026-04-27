"""Canonical playbook action vocabulary.

Single source of truth for the device-facing actions a playbook step can
take. Used by:

  • `services/playbook_ingestor.py` — Pydantic schema validation + system
    prompt enumeration (so Opus only ever generates names from this list).
  • Android `engine/PlaybookAction.kt` — mirror sealed class. Parity is
    asserted by `tests/test_playbook_action_parity.py`.

Adding or renaming an action MUST happen in one place: this file. The
ingestor regex, the Opus prompt, and the Android executor all derive from
this enum, so a rename here propagates without missing a site.

NOTE: This is the **playbook** vocabulary — what static playbook steps
look like. The agent loop's tool-use vocabulary (`tap_node`, `speak`,
`done`, `remember`, …) is intentionally a superset and lives in
`services/agent_engine.py::_TOOLS`. The two are not the same and should
not be merged.
"""

from __future__ import annotations

from enum import Enum


class PlaybookAction(str, Enum):
    """Concrete device-facing actions a playbook step can perform."""

    LAUNCH      = "launch"        # open an app by package
    TAP         = "tap"           # single click on a node
    LONG_PRESS  = "long_press"    # long-click (hold 1.2s default)
    TYPE        = "type"          # set text into a focused/editable node
    SWIPE       = "swipe"         # directional scroll
    WAIT        = "wait"          # pause for ms
    READ        = "read"          # observe a value off-screen (no side effect)
    VERIFY      = "verify"        # assert an expected node is present
    BACK        = "back"          # press Android back button
    HOME        = "home"          # press Android home button


# Regex pattern enforced by the Pydantic schema. Built from the enum so a
# rename in one place stays consistent with validation.
ACTION_REGEX: str = r"^(" + "|".join(a.value for a in PlaybookAction) + r")$"

# Human-readable list for use in LLM prompts (kept stable order).
ACTION_LIST_FOR_PROMPT: str = "|".join(a.value for a in PlaybookAction)

# Short rationales for each action — useful when we want the LLM to pick
# the right one for an observation (failure-classifier prompts later).
ACTION_DOC: dict[PlaybookAction, str] = {
    PlaybookAction.LAUNCH:     "Open an app by package name (params.package).",
    PlaybookAction.TAP:        "Click on the node matched by target_selector.",
    PlaybookAction.LONG_PRESS: "Long-click — hold for ~1.2s. Used for context menus, voice notes.",
    PlaybookAction.TYPE:       "Type params.text into the matched editable node.",
    PlaybookAction.SWIPE:      "Scroll the screen — params.direction in {up, down, left, right}.",
    PlaybookAction.WAIT:       "Pause for timeout_ms before the next step (loading screens).",
    PlaybookAction.READ:       "Read a value from the screen into params.into without side effects.",
    PlaybookAction.VERIFY:     "Assert target_selector is present; fail the step if not.",
    PlaybookAction.BACK:       "Press the Android Back button.",
    PlaybookAction.HOME:       "Press the Android Home button.",
}
