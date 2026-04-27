"""Classify a playbook-run failure into one of a small structured set.

Why this exists: an `expect_failed` log line tells us the verifier didn't
see what it wanted, but says nothing about why. Without a class label, the
nightly improvement loop has no signal to act on — Opus can't tell "the
selector is stale because the app updated" from "the user didn't grant
notification permission" without it.

Two-tier design:

  1. RULE-BASED (this module). Maps the device's `failed_reason` string
     to a class via deterministic regex/substring rules. Free, instant,
     covers the common 80%.
  2. LLM-BASED (later). For rule-based misses (`unknown`), a Haiku call
     with the failed step + screen-tree-at-time-of-failure picks the
     class. Wired separately so we control cost.

The classes are deliberately a small, stable set — they show up in the
reliability dashboard and feed the nightly Opus prompt, so adding a new
class is a deliberate decision and gets a migration if persisted.
"""

from __future__ import annotations

from enum import Enum


class FailureClass(str, Enum):
    SELECTOR_STALE       = "selector_stale"        # element moved / renamed (app updated)
    DIALOG_APPEARED      = "dialog_appeared"       # consent / permission popup blocked us
    NETWORK_ERROR        = "network_error"         # spinner never resolved, no offline state
    AMBIGUOUS_MATCH      = "ambiguous_match"       # multiple plausible nodes, refused to guess
    PERMISSION_MISSING   = "permission_missing"    # accessibility/notif perm revoked
    TIMING_ISSUE         = "timing_issue"          # action fired before screen ready
    APP_STATE_UNEXPECTED = "app_state_unexpected"  # different screen than playbook expected
    GESTURE_REJECTED     = "gesture_rejected"      # OS dropped the gesture (a11y service quirk)
    IME_ISSUE            = "ime_issue"             # keyboard / text input problem
    UNKNOWN              = "unknown"               # didn't match any rule — needs LLM fallback


# Stable list for prompts and dashboards. Ordered so most actionable
# classes come first.
FAILURE_CLASS_LIST: list[str] = [c.value for c in FailureClass]


def classify_from_reason(failed_reason: str | None) -> FailureClass:
    """Pure-rule classification from the device-side `failed_reason` string.

    The device emits structured prefixes that map cleanly:
      • `expect_failed:<kind>`           — verifier saw the wrong screen
      • `node_not_found*`                — selector didn't match
      • `gesture_rejected` / `swipe_rejected` — OS dropped the gesture
      • `set_text_failed`                — IME / EditText issue
      • `app_not_installed:*`            — user removed the app
      • `payment_app_blocked`            — blocklist tripped
      • `launched_not_foregrounded:*`    — launched but couldn't reach foreground

    Anything else falls through to UNKNOWN, where the optional LLM layer
    takes over.
    """
    if not failed_reason:
        return FailureClass.UNKNOWN
    r = failed_reason.lower()

    # Verifier-driven failures: the action ran but the post-condition
    # didn't hold. The kind suffix narrows the cause.
    if r.startswith("expect_failed:"):
        kind = r.removeprefix("expect_failed:").strip()
        # `node_disappears` not satisfied → action didn't take effect.
        # On a "Send" step that usually means a dialog ate the tap or the
        # network call is still in-flight. Bias to dialog (more common).
        if kind == "node_disappears":
            return FailureClass.DIALOG_APPEARED
        # `screen_contains` / `node_appears` not satisfied → either the
        # selector pointed at the right thing but the screen drifted, or
        # the next screen never loaded. Lean SELECTOR_STALE because that
        # is what the nightly Opus job can actually patch.
        if kind in ("screen_contains", "node_appears"):
            return FailureClass.SELECTOR_STALE
        # `field_value` mismatch → IME swallowed the text or wrong field.
        if kind == "field_value":
            return FailureClass.IME_ISSUE
        # `screen_changed` failed → the action didn't move state. Most
        # often a TIMING_ISSUE (we polled too soon) or an unexpected app
        # state. Default to TIMING; rule layer can be tuned later.
        if kind == "screen_changed":
            return FailureClass.TIMING_ISSUE
        return FailureClass.UNKNOWN

    if "node_not_found_strict_confirm_required" in r:
        # Confidence floor refused to guess on an irreversible step —
        # genuinely safer to call this AMBIGUOUS than SELECTOR_STALE.
        return FailureClass.AMBIGUOUS_MATCH
    if "node_not_found" in r or "no_target" in r or "verify_node_missing" in r:
        return FailureClass.SELECTOR_STALE

    if r.startswith("set_text_failed") or r.startswith("missing_text"):
        return FailureClass.IME_ISSUE

    if r.startswith("gesture_rejected") or r.startswith("swipe_rejected") \
            or r.startswith("perform_action_returned_false"):
        return FailureClass.GESTURE_REJECTED

    if r.startswith("app_not_installed") or r.startswith("launched_not_foregrounded"):
        return FailureClass.APP_STATE_UNEXPECTED

    if r.startswith("listener_not_running") or "permission" in r:
        return FailureClass.PERMISSION_MISSING

    if "timeout" in r or "deadline" in r:
        return FailureClass.TIMING_ISSUE

    if "unknown_action" in r or "unknown_tool" in r:
        # Should be impossible after the action-vocab unify task, but if
        # it happens it's our schema drift, not the user's environment.
        return FailureClass.APP_STATE_UNEXPECTED

    return FailureClass.UNKNOWN
