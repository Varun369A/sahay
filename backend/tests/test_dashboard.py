"""Tests for the dashboard service.

The DB-touching aggregations need a real Postgres + RLS setup; they're
covered by integration tests separately. Here we lock down:
  • the pure helpers (`safe_rate`)
  • the endpoint is registered, gated, and returns the right shape
    (verified via OpenAPI introspection — no DB needed)
"""

from __future__ import annotations

import pytest

from sahay.services.dashboard import safe_rate


# ─── Pure helpers ───────────────────────────────────────────────────


def test_safe_rate_zero_total_returns_zero() -> None:
    assert safe_rate(0, 0) == 0.0


def test_safe_rate_basic() -> None:
    assert safe_rate(50, 100) == 0.5
    assert safe_rate(99, 100) == 0.99
    assert safe_rate(100, 100) == 1.0


def test_safe_rate_no_div_by_zero_even_with_successes() -> None:
    """Defensive: caller passing successes>0 with total=0 is broken
    upstream, but we still return 0.0 instead of exploding."""
    assert safe_rate(5, 0) == 0.0


@pytest.mark.parametrize(
    "s,t,expected",
    [
        (0, 1, 0.0),
        (1, 1, 1.0),
        (1, 4, 0.25),
        (3, 4, 0.75),
    ],
)
def test_safe_rate_table(s: int, t: int, expected: float) -> None:
    assert safe_rate(s, t) == pytest.approx(expected)


# ─── Endpoint registration / shape ──────────────────────────────────


def test_dashboard_endpoint_in_openapi() -> None:
    """The endpoint must be wired into the FastAPI app — guards against
    forgetting to call `app.include_router(dashboard.router)`."""
    from sahay.main import app

    paths = app.openapi().get("paths", {})
    assert "/v1/admin/dashboard" in paths, sorted(paths.keys())


def test_dashboard_endpoint_is_get_only() -> None:
    from sahay.main import app

    methods = list(app.openapi()["paths"]["/v1/admin/dashboard"].keys())
    assert "get" in methods
    assert "post" not in methods
    assert "delete" not in methods


def test_dashboard_response_schema_complete() -> None:
    """The response_model registers a Pydantic schema; OpenAPI surfaces
    its top-level keys. Lock the keyset so a future refactor that
    drops a section (e.g. canaries) fails the test."""
    from sahay.main import app

    openapi = app.openapi()
    out_ref = openapi["paths"]["/v1/admin/dashboard"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    schema_name = out_ref.rsplit("/", 1)[-1]
    schema = openapi["components"]["schemas"][schema_name]
    assert "properties" in schema
    expected = {
        "generated_at",
        "headline",
        "intents",
        "failure_classes",
        "canaries",
        "recent_promotions",
    }
    assert set(schema["properties"].keys()) == expected


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client) -> None:
    """Unauthenticated requests get 401, not 200 — the dashboard exposes
    aggregate ops data; it must never be public."""
    resp = await client.get("/v1/admin/dashboard")
    assert resp.status_code in (401, 403, 422), resp.status_code
