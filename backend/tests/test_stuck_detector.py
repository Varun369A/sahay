"""Unit tests for the agent-loop stuck detector.

The detector is the gate to the Opus-4.7 rescue path — false positives
waste money on a $$$ model, false negatives let the agent dig itself
deeper. Lock it down with a small but exhaustive case list.
"""

from __future__ import annotations

from sahay.services.agent_engine import _NON_STUCK_TOOLS, _detect_stuck
from sahay.services.conversation_memory import Turn


def _t(tool: str, args: str = "", outcome: str = "dispatched", step: int = 0) -> Turn:
    return Turn(
        ts=0.0, step=step, tool=tool, args_summary=args, outcome=outcome,
    )


def test_empty_history_not_stuck() -> None:
    assert _detect_stuck([]) is None


def test_single_turn_not_stuck() -> None:
    assert _detect_stuck([_t("tap_node", "i=7")]) is None


def test_two_identical_taps_is_stuck() -> None:
    history = [_t("tap_node", "i=7", step=0), _t("tap_node", "i=7", step=1)]
    stuck = _detect_stuck(history)
    assert stuck is not None
    assert stuck.tool == "tap_node"
    assert stuck.step == 1


def test_two_different_targets_not_stuck() -> None:
    history = [_t("tap_node", "i=7"), _t("tap_node", "i=8")]
    assert _detect_stuck(history) is None


def test_two_different_tools_not_stuck() -> None:
    history = [_t("tap_node", "i=7"), _t("swipe", "down")]
    assert _detect_stuck(history) is None


def test_done_outcome_not_stuck() -> None:
    """Don't flag rescue if the second attempt actually succeeded as the
    final task action — the conversation is over, no rescue needed."""
    history = [_t("tap_node", "i=7"), _t("tap_node", "i=7", outcome="done")]
    assert _detect_stuck(history) is None


def test_speak_repetition_is_not_stuck() -> None:
    """Two `speak`s in a row is just narration, not a loop."""
    history = [_t("speak", "msg=hi"), _t("speak", "msg=hi again")]
    assert _detect_stuck(history) is None


def test_wait_repetition_is_not_stuck() -> None:
    history = [_t("wait", "1000ms"), _t("wait", "1000ms")]
    assert _detect_stuck(history) is None


def test_ask_user_repetition_is_not_stuck() -> None:
    history = [_t("ask_user", "which one?"), _t("ask_user", "still which one?")]
    assert _detect_stuck(history) is None


def test_repeated_action_after_chatty_turns_still_detects() -> None:
    """A user-visible `speak` between two failed taps shouldn't hide the
    stuck loop — physical actions are filtered before pairing."""
    history = [
        _t("tap_node", "i=7", step=0),
        _t("speak", "let me try again", step=1),
        _t("tap_node", "i=7", step=2),
    ]
    stuck = _detect_stuck(history)
    assert stuck is not None
    assert stuck.step == 2


def test_non_stuck_tool_set_is_complete() -> None:
    """Lock the set of "conversational / server-side" tools that are
    excluded from stuck checking. Adding a new tool should be a
    deliberate decision, not silently change behavior."""
    expected = {
        "speak", "ask_user", "wait", "done", "give_up",
        "remember", "forget", "note_followup", "stage_reply",
        "create_document",
    }
    assert set(_NON_STUCK_TOOLS) == expected


def test_pixel_tap_repeats_also_detected() -> None:
    """The pixel-coord `tap` fallback is just as stuck-prone as `tap_node`."""
    history = [_t("tap", "x=120 y=300"), _t("tap", "x=120 y=300")]
    stuck = _detect_stuck(history)
    assert stuck is not None
    assert stuck.tool == "tap"
