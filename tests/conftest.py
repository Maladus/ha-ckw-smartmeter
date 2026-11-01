"""Shared pytest configuration.

``pytest-homeassistant-custom-component`` (PHCC) installs Home Assistant's event
loop policy and assumes every test has a current loop when its autouse fixtures
run. The mock API tests (#144) are plain ``unittest.IsolatedAsyncioTestCase``
classes: they bring and close their own loop, and they bind an ephemeral localhost
socket which PHCC blocks. The two hooks below restore exactly what those tests
need, without changing them and without taking anything away from tests that do
use Home Assistant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Final

import pytest
import pytest_socket

MOCK_TEST_MODULE: Final = "test_mock_api.py"

# One loop kept open for the session: Home Assistant's policy refuses to create
# one implicitly, so it has to exist before the plugin's fixtures ask for it.
FALLBACK_LOOP: Final[asyncio.AbstractEventLoop] = asyncio.new_event_loop()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Make a loop current before any autouse fixture asks for one.

    ``IsolatedAsyncioTestCase`` closes its runner's loop and leaves the thread
    without one, which Home Assistant's policy reports as a RuntimeError. Tests
    that use pytest-asyncio install their own loop later in the same setup.
    """
    asyncio.set_event_loop(FALLBACK_LOOP)


@pytest.fixture(autouse=True)
def allow_local_sockets(request: pytest.FixtureRequest) -> Generator[None]:
    """Re-enable sockets for the mock API tests, which serve on localhost."""
    if request.path.name == MOCK_TEST_MODULE:
        pytest_socket.enable_socket()
    yield


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Close the fallback loop so the interpreter exits without a warning."""
    if not FALLBACK_LOOP.is_closed():
        asyncio.set_event_loop(None)
        FALLBACK_LOOP.close()
