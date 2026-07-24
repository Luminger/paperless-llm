"""Shared fixtures for the integration + live tiers: ad-hoc
paperless-ngx via podman compose. Loaded as a conftest plugin.

By default expects the instance to already run (started manually with
``podman compose -f deploy/test/compose.yaml up -d``). Set
``PLLM_TEST_MANAGE_COMPOSE=1`` to have the fixture start/stop it.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

PAPERLESS_TEST_URL = os.environ.get("PLLM_TEST_PAPERLESS_URL", "http://127.0.0.1:8200")
COMPOSE_FILE = (
    Path(__file__).resolve().parents[2] / "deploy" / "test" / "compose.yaml"
)


def _wait_healthy(timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{PAPERLESS_TEST_URL}/api/", timeout=5)
            # 302: newer paperless (>= 2.20) redirects /api/ to the schema
            # view for anonymous requests — the server is up either way.
            if r.status_code in (200, 302, 401, 403):
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


@pytest.fixture(scope="session")
def paperless_instance():
    managed = os.environ.get("PLLM_TEST_MANAGE_COMPOSE") == "1"
    if managed:
        subprocess.run(
            ["podman", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], check=True
        )
    if not _wait_healthy():
        pytest.skip(
            f"no paperless-ngx reachable at {PAPERLESS_TEST_URL}; start it with "
            f"`podman compose -f {COMPOSE_FILE} up -d`"
        )
    yield PAPERLESS_TEST_URL
    if managed:
        subprocess.run(
            ["podman", "compose", "-f", str(COMPOSE_FILE), "down", "-v"], check=False
        )


@pytest.fixture(scope="session")
def paperless_token(paperless_instance) -> str:
    r = httpx.post(
        f"{paperless_instance}/api/token/",
        data={"username": "admin", "password": "admin"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture(scope="session")
def seeded(paperless_instance, paperless_token) -> str:
    """Seed the corpus once per test session (idempotent-ish)."""
    import asyncio

    from app.seeding import seed_corpus

    asyncio.run(seed_corpus(paperless_instance, paperless_token, wait=True))
    return paperless_instance
