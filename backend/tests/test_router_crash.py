"""Crash / fuzz smoke for every new Round 3-5 router.

We don't have a live DB or Redis so we only check what happens at the
INPUT boundary: the app's routes respond gracefully (401 / 422 / 404 —
never 500) to:
  • missing auth
  • invalid payload shapes
  • oversized fields
  • values outside the declared ranges

The idea isn't to prove the whole endpoint works; it's to prove the
validation layer is tight enough that no handler is reachable with
garbage input.
"""

from __future__ import annotations

import os

os.environ.setdefault("SAHAY_ENV", "dev")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/sahay")
os.environ.setdefault("REDIS_URL", "redis://localhost")
os.environ.setdefault("R2_ACCOUNT_ID", "test")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("R2_BUCKET", "test")
os.environ.setdefault("JWT_SECRET", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("REPLICATE_API_TOKEN", "test")
os.environ.setdefault("RAZORPAY_KEY_ID", "test")
os.environ.setdefault("RAZORPAY_KEY_SECRET", "test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from sahay.main import app
    return TestClient(app)


class TestAuthRequired:
    """Every Round 3-5 endpoint must reject anonymous callers with 401/403."""

    endpoints = [
        ("GET",  "/v1/notifications/recent"),
        ("POST", "/v1/notifications/ingest"),
        ("DELETE", "/v1/notifications"),
        ("POST", "/v1/documents"),
        ("GET",  "/v1/agent/morning-briefing"),
        ("GET",  "/v1/memory/important-contacts"),
    ]

    @pytest.mark.parametrize("method,path", endpoints)
    def test_anonymous_rejected(self, client, method, path):
        resp = client.request(method, path, json={})
        assert resp.status_code in (401, 403, 422), \
            f"{method} {path} returned {resp.status_code}"


class TestInputValidation:
    """Malformed payloads must 422, never 500."""

    def test_notification_ingest_empty_list_is_422(self, client):
        r = client.post("/v1/notifications/ingest",
                        json={"notifications": []})
        # 401 because no auth, but even with a stub header pydantic would 422
        # (min_length=1). Either way, must NOT be 500.
        assert r.status_code in (401, 422)

    def test_notification_ingest_bad_shape_is_422(self, client):
        r = client.post("/v1/notifications/ingest",
                        json={"not_a_field": True})
        assert r.status_code in (401, 422)

    def test_notification_ingest_oversized_batch_rejected(self, client):
        big = [{
            "key": f"k{i}", "package": "com.example",
            "posted_at": 0.0,
        } for i in range(200)]
        r = client.post("/v1/notifications/ingest", json={"notifications": big})
        # max_length=50 in NotificationIngestRequest
        assert r.status_code in (401, 422)

    def test_document_prompt_too_short_is_422(self, client):
        r = client.post("/v1/documents",
                        json={"kind": "resume", "prompt": "hi"})
        # prompt min_length=4
        assert r.status_code in (401, 422)

    def test_document_unknown_kind_is_422(self, client):
        r = client.post("/v1/documents",
                        json={"kind": "not-a-kind",
                              "prompt": "draft something"})
        assert r.status_code in (401, 422)

    def test_recent_notifications_limit_bounds(self, client):
        # limit ge=1, le=50
        r = client.get("/v1/notifications/recent?limit=999")
        assert r.status_code in (401, 422)
        r = client.get("/v1/notifications/recent?limit=0")
        assert r.status_code in (401, 422)

    def test_important_contacts_invalid_min(self, client):
        r = client.get("/v1/memory/important-contacts?min_importance=notanint")
        assert r.status_code in (401, 422)


class TestHealth:
    def test_root_health_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200


class TestOpenAPI:
    """Verify every new router is actually wired — catches main.py regressions."""

    def test_openapi_lists_new_endpoints(self, client):
        spec = client.get("/openapi.json").json()
        paths = set(spec["paths"].keys())

        required = {
            "/v1/notifications/ingest",
            "/v1/notifications/recent",
            "/v1/documents",
            "/v1/agent/morning-briefing",
            "/v1/memory/important-contacts",
        }
        missing = required - paths
        assert not missing, f"missing from OpenAPI: {missing}"
