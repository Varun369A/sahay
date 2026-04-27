"""Rule-based classifier tests.

The classifier feeds the dashboard and the nightly Opus prompt, so its
output labels are part of the public contract. Tighten the rule mapping
here so a future change has to be deliberate.
"""

from __future__ import annotations

import pytest

from sahay.services.failure_classifier import (
    FAILURE_CLASS_LIST,
    FailureClass,
    classify_from_reason,
)


# ─── Empty / null inputs ────────────────────────────────────────────


def test_none_reason_is_unknown() -> None:
    assert classify_from_reason(None) is FailureClass.UNKNOWN


def test_empty_string_is_unknown() -> None:
    assert classify_from_reason("") is FailureClass.UNKNOWN


# ─── expect_failed:<kind> branches ──────────────────────────────────


def test_expect_failed_node_disappears_is_dialog() -> None:
    """`Send` button still on screen after a tap → most often a dialog
    intercepted us. Net win even on the false positives because the
    nightly Opus job's first move on DIALOG is "add a dismiss step"."""
    assert (
        classify_from_reason("expect_failed:node_disappears")
        is FailureClass.DIALOG_APPEARED
    )


def test_expect_failed_screen_contains_is_selector_stale() -> None:
    assert (
        classify_from_reason("expect_failed:screen_contains")
        is FailureClass.SELECTOR_STALE
    )


def test_expect_failed_node_appears_is_selector_stale() -> None:
    assert (
        classify_from_reason("expect_failed:node_appears")
        is FailureClass.SELECTOR_STALE
    )


def test_expect_failed_field_value_is_ime() -> None:
    assert (
        classify_from_reason("expect_failed:field_value")
        is FailureClass.IME_ISSUE
    )


def test_expect_failed_screen_changed_is_timing() -> None:
    assert (
        classify_from_reason("expect_failed:screen_changed")
        is FailureClass.TIMING_ISSUE
    )


def test_expect_failed_unknown_kind_is_unknown() -> None:
    assert (
        classify_from_reason("expect_failed:something_new")
        is FailureClass.UNKNOWN
    )


# ─── Selector / element resolution branches ─────────────────────────


def test_strict_confirm_required_is_ambiguous() -> None:
    """The confidence-floor refusal — we KNOW we couldn't safely pick a
    target on an irreversible step. AMBIGUOUS captures that better than
    SELECTOR_STALE."""
    assert (
        classify_from_reason("node_not_found_strict_confirm_required")
        is FailureClass.AMBIGUOUS_MATCH
    )


def test_node_not_found_is_selector_stale() -> None:
    assert classify_from_reason("node_not_found") is FailureClass.SELECTOR_STALE


def test_no_target_is_selector_stale() -> None:
    assert classify_from_reason("no_target") is FailureClass.SELECTOR_STALE


def test_verify_node_missing_is_selector_stale() -> None:
    assert (
        classify_from_reason("verify_node_missing") is FailureClass.SELECTOR_STALE
    )


# ─── IME / gesture / app-state ──────────────────────────────────────


def test_set_text_failed_is_ime() -> None:
    assert classify_from_reason("set_text_failed") is FailureClass.IME_ISSUE


def test_missing_text_is_ime() -> None:
    assert classify_from_reason("missing_text") is FailureClass.IME_ISSUE


def test_gesture_rejected_is_gesture_rejected() -> None:
    assert (
        classify_from_reason("gesture_rejected") is FailureClass.GESTURE_REJECTED
    )
    assert (
        classify_from_reason("swipe_rejected") is FailureClass.GESTURE_REJECTED
    )
    assert (
        classify_from_reason("perform_action_returned_false")
        is FailureClass.GESTURE_REJECTED
    )


def test_app_not_installed_is_app_state() -> None:
    assert (
        classify_from_reason("app_not_installed:com.example.app")
        is FailureClass.APP_STATE_UNEXPECTED
    )


def test_launched_not_foregrounded_is_app_state() -> None:
    assert (
        classify_from_reason("launched_not_foregrounded:com.netflix")
        is FailureClass.APP_STATE_UNEXPECTED
    )


def test_listener_not_running_is_permission() -> None:
    assert (
        classify_from_reason("listener_not_running")
        is FailureClass.PERMISSION_MISSING
    )


def test_permission_substring_triggers_permission() -> None:
    assert (
        classify_from_reason("missing_accessibility_permission")
        is FailureClass.PERMISSION_MISSING
    )


def test_timeout_is_timing() -> None:
    assert (
        classify_from_reason("polling_timeout_after_3000ms")
        is FailureClass.TIMING_ISSUE
    )


def test_unknown_action_is_app_state() -> None:
    """If the device sees an action it doesn't recognise, that's our
    schema drift, not the user's environment. Tag it so we can see it
    on the dashboard and fix it fast."""
    assert (
        classify_from_reason("unknown_action:scroll")
        is FailureClass.APP_STATE_UNEXPECTED
    )


# ─── Catch-all sanity ───────────────────────────────────────────────


def test_random_string_is_unknown() -> None:
    assert classify_from_reason("totally_unhandled_thing") is FailureClass.UNKNOWN


def test_class_list_is_stable_set() -> None:
    """Adding a new class is a deliberate decision — pin the set so a
    drive-by change has to update this test."""
    expected = {
        "selector_stale", "dialog_appeared", "network_error",
        "ambiguous_match", "permission_missing", "timing_issue",
        "app_state_unexpected", "gesture_rejected", "ime_issue", "unknown",
    }
    assert set(FAILURE_CLASS_LIST) == expected


@pytest.mark.parametrize("class_value", FAILURE_CLASS_LIST)
def test_every_class_value_round_trips_to_enum(class_value: str) -> None:
    """Every string in the public list must correspond to a real enum
    member — guards against typos in `FAILURE_CLASS_LIST`."""
    assert FailureClass(class_value).value == class_value
