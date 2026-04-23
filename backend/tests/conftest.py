"""Pytest fixtures.

Most fixtures are added in subsequent steps (db, async client, auth user).
This file currently provides only the basic test client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from sahay.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Bare ASGI test client. No auth, no DB overrides."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
