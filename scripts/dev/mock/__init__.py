"""Local mock of the CKW OpenEnergy API and its fake Keycloak.

Public entry point for tests and tooling: :func:`build_app` returns one aiohttp
application that serves ``/api/v1/...`` and ``/realms/...`` on a single port.
"""

from __future__ import annotations

from scripts.dev.mock.app import build_app

__all__ = ["build_app"]
