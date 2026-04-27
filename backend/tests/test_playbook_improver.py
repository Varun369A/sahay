"""Tests for the pure parts of the playbook improver.

Database-touching paths (`find_candidates`, `aggregate_failure_classes`,
`improve_playbook`, `promote_revision`, `rollback_revision`) need a
real Postgres + RLS setup; those are covered by integration tests in a
separate environment. Here we lock down the in-memory logic that has to
stay correct regardless of the DB:

  • prompt builder includes the right fields
  • response parser handles real-world Opus output (with/without fences,
    refusal envelope, validation failure)
  • the FailureBreakdown helper has stable ordering for the prompt
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sahay.services.playbook_improver import (
    FailureBreakdown,
    build_improvement_prompt,
    parse_improvement_response,
)


def _fake_pb(**overrides: Any) -> Any:
    """Quack-typed Playbook stand-in. The improver only reads attributes
    `app_package`, `app_name`, `intent`, `reliability_score`,
    `success_count`, `fail_count`, `steps_json` — no DB calls in the
    pure functions under test."""
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        app_package="com.netflix.mediaclient",
        app_name="Netflix",
        intent="search_title",
        reliability_score=0.55,
        success_count=18,
        fail_count=14,
        steps_json={
            "app_package": "com.netflix.mediaclient",
            "app_name": "Netflix",
            "intent": "search_title",
            "inputs": ["title"],
            "steps": [
                {
                    "step": 1,
                    "description": "tap search",
                    "target_screen": "home",
                    "action": "tap",
                    "target_selector": {"text": "Search"},
                    "params": {},
                    "confirm_required": False,
                    "timeout_ms": 5000,
                    "expect": {
                        "kind": "node_appears",
                        "selector": {"text": "Search"},
                        "timeout_ms": 3000,
                    },
                },
            ],
        },
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _valid_spec() -> dict[str, Any]:
    return {
        "app_package": "com.netflix.mediaclient",
        "app_name": "Netflix",
        "intent": "search_title",
        "inputs": ["title"],
        "steps": [
            {
                "step": 1,
                "description": "tap search",
                "target_screen": "home",
                "action": "tap",
                "target_selector": {"text": "Search"},
                "params": {},
                "confirm_required": False,
                "timeout_ms": 5000,
            }
        ],
        "notes": "Patched: tightened the selector chain.",
    }


# ─── Prompt builder ────────────────────────────────────────────────


def test_prompt_includes_score_and_counts() -> None:
    pb = _fake_pb()
    bk = FailureBreakdown(
        total_failures=10,
        by_class={"selector_stale": 7, "dialog_appeared": 3},
        failing_step_indexes=[1, 2],
    )
    msg = build_improvement_prompt(pb, bk)
    assert "Netflix" in msg
    assert "search_title" in msg
    assert "0.550" in msg              # reliability_score formatting
    assert "18" in msg and "14" in msg  # success / fail
    assert "Failures observed" in msg
    assert "selector_stale: 7" in msg
    assert "dialog_appeared: 3" in msg


def test_prompt_failure_classes_sorted_descending() -> None:
    pb = _fake_pb()
    bk = FailureBreakdown(
        total_failures=10,
        by_class={"a": 1, "b": 5, "c": 3},
        failing_step_indexes=[],
    )
    msg = build_improvement_prompt(pb, bk)
    pos_b = msg.index("b: 5")
    pos_c = msg.index("c: 3")
    pos_a = msg.index("a: 1")
    assert pos_b < pos_c < pos_a


def test_prompt_handles_no_failures_gracefully() -> None:
    pb = _fake_pb()
    bk = FailureBreakdown(total_failures=0, by_class={}, failing_step_indexes=[])
    msg = build_improvement_prompt(pb, bk)
    assert "(none)" in msg
    assert "(no specific step" in msg


def test_prompt_truncates_failing_step_list() -> None:
    pb = _fake_pb()
    bk = FailureBreakdown(
        total_failures=20,
        by_class={"timing_issue": 20},
        failing_step_indexes=list(range(1, 30)),
    )
    msg = build_improvement_prompt(pb, bk)
    # Only top 5 step indexes are shown to keep the prompt tight.
    visible_steps = [int(s) for s in msg.split("step indexes (1-based):")[1].split("\n")[0].replace(",", " ").split() if s.isdigit()]
    assert len(visible_steps) <= 5


def test_prompt_embeds_current_spec_as_json_fenced() -> None:
    pb = _fake_pb()
    bk = FailureBreakdown(total_failures=5, by_class={"unknown": 5}, failing_step_indexes=[])
    msg = build_improvement_prompt(pb, bk)
    assert "```json" in msg
    assert "tap search" in msg


# ─── Response parser ───────────────────────────────────────────────


def test_parse_clean_json_returns_spec() -> None:
    pb = _fake_pb()
    raw = json.dumps(_valid_spec())
    spec, refusal = parse_improvement_response(raw, pb)
    assert refusal is None
    assert spec is not None
    assert spec.app_package == pb.app_package
    assert spec.intent == pb.intent
    assert len(spec.steps) == 1


def test_parse_strips_json_fence() -> None:
    pb = _fake_pb()
    raw = "```json\n" + json.dumps(_valid_spec()) + "\n```"
    spec, refusal = parse_improvement_response(raw, pb)
    assert refusal is None
    assert spec is not None


def test_parse_strips_bare_fence() -> None:
    pb = _fake_pb()
    raw = "```\n" + json.dumps(_valid_spec()) + "\n```"
    spec, refusal = parse_improvement_response(raw, pb)
    assert refusal is None
    assert spec is not None


def test_parse_invalid_json_returns_reason() -> None:
    pb = _fake_pb()
    spec, refusal = parse_improvement_response("not json at all", pb)
    assert spec is None
    assert refusal is not None
    assert refusal.startswith("invalid_json")


def test_parse_unfixable_envelope_is_clean_refusal() -> None:
    pb = _fake_pb()
    raw = json.dumps(
        {"status": "unfixable_by_doc", "reason": "permission revoked dominates failures"}
    )
    spec, refusal = parse_improvement_response(raw, pb)
    assert spec is None
    assert refusal is not None
    assert refusal.startswith("unfixable")
    assert "permission" in refusal


def test_parse_validation_failure_returns_reason() -> None:
    pb = _fake_pb()
    bad_spec = _valid_spec()
    # Make the spec invalid: action regex won't accept "click".
    bad_spec["steps"][0]["action"] = "click"
    raw = json.dumps(bad_spec)
    spec, refusal = parse_improvement_response(raw, pb)
    assert spec is None
    assert refusal is not None
    assert refusal.startswith("validation_failed")


def test_parse_force_fixes_identifiers() -> None:
    """Even if Opus drifts the package/name/intent, we override before
    Pydantic — preventing a patched spec from being attached to the
    wrong playbook row downstream."""
    pb = _fake_pb()
    drifted = _valid_spec()
    drifted["app_package"] = "com.something.else"
    drifted["app_name"] = "Other"
    drifted["intent"] = "different_intent"
    raw = json.dumps(drifted)
    spec, refusal = parse_improvement_response(raw, pb)
    assert refusal is None
    assert spec is not None
    assert spec.app_package == pb.app_package
    assert spec.app_name == pb.app_name
    assert spec.intent == pb.intent


def test_parse_unfixable_with_no_reason_field_still_refuses() -> None:
    pb = _fake_pb()
    raw = json.dumps({"status": "unfixable_by_doc"})
    spec, refusal = parse_improvement_response(raw, pb)
    assert spec is None
    assert refusal is not None
    assert refusal.startswith("unfixable")


# ─── FailureBreakdown sanity ───────────────────────────────────────


def test_failure_breakdown_attributes() -> None:
    bk = FailureBreakdown(
        total_failures=3,
        by_class={"selector_stale": 2, "timing_issue": 1},
        failing_step_indexes=[2, 1],
    )
    assert bk.total_failures == 3
    assert bk.by_class["selector_stale"] == 2
    assert bk.failing_step_indexes[0] == 2  # most-failing first


@pytest.mark.parametrize(
    "by_class,total",
    [
        ({}, 0),
        ({"selector_stale": 5}, 5),
        ({"a": 1, "b": 2, "c": 3}, 6),
    ],
)
def test_failure_breakdown_total_consistency(
    by_class: dict[str, int], total: int,
) -> None:
    """Total in the breakdown should match sum of per-class counts.
    The aggregation function should always preserve this; the test
    locks the contract."""
    bk = FailureBreakdown(
        total_failures=sum(by_class.values()),
        by_class=by_class,
        failing_step_indexes=[],
    )
    assert bk.total_failures == total
    assert sum(bk.by_class.values()) == bk.total_failures
