"""Tests for the local mock CKW API and its fake Keycloak.

``pytest-aiohttp`` is not a dependency of this repository, so the tests drive
``aiohttp.test_utils.TestServer``/``TestClient`` through ``IsolatedAsyncioTestCase``.
Everything here is offline: the mock reads the committed fixtures and binds an
ephemeral localhost port.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Final

from aiohttp.test_utils import TestClient, TestServer

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.dev.mock import build_app

AUTH: Final[dict[str, str]] = {"Authorization": "Bearer mock-token"}
CUSTOMER_URL: Final[str] = "/api/v1/smartmeter/customer"
MEASUREMENT_URL: Final[str] = "/api/v1/smartmeter/measurement"
AUTHORIZE_URL: Final[str] = "/realms/CKW-prod/protocol/openid-connect/auth"
TOKEN_URL: Final[str] = "/realms/CKW-prod/protocol/openid-connect/token"
OTP_URL: Final[str] = "/realms/CKW-prod/otp/login"


class MockApiTest(unittest.IsolatedAsyncioTestCase):
    """One mock instance per test, so in-memory IdP state never leaks."""

    client: TestClient

    async def asyncSetUp(self) -> None:
        self.client = TestClient(TestServer(build_app()))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_customer_requires_bearer_token(self) -> None:
        """Without a bearer token the customer endpoint answers 401 as JSON."""
        response = await self.client.get(CUSTOMER_URL)
        assert response.status == 401
        body = await response.json()
        assert body["error"] == "unauthorized"
        assert response.headers["x-ratelimit-limit"] == "100"

        response = await self.client.get(CUSTOMER_URL, headers=AUTH)
        assert response.status == 200
        body = await response.json()
        location = body["customer"]["locations"][0]
        assert location["address"] == {
            "street_address": "Musterweg 1",
            "postal_code": "6000",
            "city": "Luzern",
        }
        assert len(location["meters"]) == 2

    async def test_measurement_slice_returns_requested_days(self) -> None:
        """The measurement endpoint slices the fixture by local calendar day."""
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20260922/20261031/day", headers=AUTH
        )
        assert response.status == 200
        body = await response.json()
        assert len(body) == 2
        assert all(len(series["measurements"]) == 40 for series in body)

        response = await self.client.get(
            f"{MEASUREMENT_URL}/20261001/20261010/days", headers=AUTH
        )
        measurements = (await response.json())[0]["measurements"]
        assert len(measurements) == 10
        assert measurements[0]["zeitstempel_von_utc"] == "2026-09-30T22:00:00"

    async def test_measurement_crosses_daylight_saving_change(self) -> None:
        """2026-10-25 is a 25 hour day in Europe/Zurich and the fixture says so."""
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20261025/20261025/day", headers=AUTH
        )
        entry = (await response.json())[0]["measurements"][0]
        assert entry["zeitstempel_von_utc"] == "2026-10-24T22:00:00"
        assert entry["zeitstempel_bis_utc"] == "2026-10-25T23:00:00"

    async def test_measurement_lists_every_meter_with_one_quality_flag(self) -> None:
        """One element per delivery point, and exactly one implausible interval."""
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20260922/20261031/day", headers=AUTH
        )
        body = await response.json()
        series = {item["deliverypointnumber"]: item["measurements"] for item in body}
        assert set(series) == {
            "CH0000000000000000000000000000001",
            "CH0000000000000000000000000000002",
        }
        assert all(
            entry["anzahl_linien_p"] == 1
            for entry in series["CH0000000000000000000000000000002"]
        )
        flagged = [
            entry
            for entry in series["CH0000000000000000000000000000001"]
            if entry["anzahl_linien_p"] == 2
        ]
        assert len(flagged) == 1
        assert flagged[0]["zeitstempel_von_utc"] == "2026-10-01T22:00:00"

    async def test_unsupported_aggregation_returns_400(self) -> None:
        """The documented enum is ten values; anything else is rejected."""
        for aggregation in ("hour", "hours", "Day", "day(s)"):
            response = await self.client.get(
                f"{MEASUREMENT_URL}/20260922/20261031/{aggregation}", headers=AUTH
            )
            assert response.status == 400, aggregation
            assert (await response.json())["error"] == "bad_request"

        response = await self.client.get(
            f"{MEASUREMENT_URL}/20260922/20261031/month", headers=AUTH
        )
        assert response.status == 200

    async def test_fail_injection_returns_status_with_rate_limit_headers(self) -> None:
        """``?fail=429`` answers 429 and still reports the rate-limit state."""
        response = await self.client.get(f"{CUSTOMER_URL}?fail=429", headers=AUTH)
        assert response.status == 429
        assert response.headers["x-ratelimit-limit"] == "100"
        assert int(response.headers["x-ratelimit-remaining"]) >= 0
        assert (await response.json())["error"] == "too_many_requests"

        response = await self.client.get(
            f"{CUSTOMER_URL}?ratelimit-remaining=0&ratelimit-reset=2000", headers=AUTH
        )
        assert response.status == 200
        assert response.headers["x-ratelimit-remaining"] == "0"
        assert response.headers["x-ratelimit-reset"] == "2000"

        response = await self.client.get(f"{CUSTOMER_URL}?fail=418", headers=AUTH)
        assert response.status == 400

    async def test_token_endpoint_rejects_password_grant_without_code(self) -> None:
        """Mirrors the SMS/OTP variant: a password alone is never enough."""
        response = await self.client.post(
            TOKEN_URL,
            data={
                "grant_type": "password",
                "username": "mock@example.invalid",
                "password": "not-a-real-password",
            },
        )
        assert response.status == 403
        assert (await response.json())["error"] == "access_denied"

    async def test_authorize_redirects_with_code_and_state(self) -> None:
        """No login form: the auth endpoint consents instantly and redirects."""
        response = await self.client.get(
            AUTHORIZE_URL,
            params={
                "client_id": "ckw-dev-mock",
                "redirect_uri": "http://localhost:8123/auth/external/callback",
                "response_type": "code",
                "state": "mock-state",
            },
            allow_redirects=False,
        )
        assert response.status == 302
        location = response.headers["Location"]
        assert location.startswith("http://localhost:8123/auth/external/callback?code=")
        assert "state=mock-state" in location

        code = location.split("code=")[1].split("&")[0]
        response = await self.client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://localhost:8123/auth/external/callback",
            },
        )
        assert response.status == 200
        body = await response.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 600
        assert body["refresh_expires_in"] == 21600
        assert body["access_token"].count(".") == 2

        response = await self.client.post(
            TOKEN_URL, data={"grant_type": "authorization_code", "code": code}
        )
        assert response.status == 400

    async def test_otp_login_is_usable_once(self) -> None:
        """``/otp/login`` records the OTP, and one token exchange consumes it."""
        response = await self.client.post(OTP_URL, json={"code": "123456"})
        assert response.status == 204
        assert response.headers["x-mock-otp"] == "123456"

        response = await self.client.post(
            TOKEN_URL, data={"grant_type": "password", "code": "123456"}
        )
        assert response.status == 200

        response = await self.client.post(
            TOKEN_URL, data={"grant_type": "password", "code": "123456"}
        )
        assert response.status == 400

    async def test_dynamic_prices_returns_24_hourly_entries(self) -> None:
        """The dynamic-price endpoint answers one plausible price per hour."""
        response = await self.client.get(
            "/api/v1/energie/dynamische-preise", headers=AUTH
        )
        assert response.status == 200
        body = await response.json()
        assert len(body["prices"]) == 24
        assert body["prices"][0]["zeitstempel_von_utc"] == "2026-10-31T23:00:00"
        assert all(0.05 < entry["preis_chf_kwh"] < 0.60 for entry in body["prices"])

    async def test_unknown_endpoint_answers_json_404(self) -> None:
        """A typo in a URL must not return an HTML error page."""
        response = await self.client.get("/api/v1/nope")
        assert response.status == 404
        assert (await response.json())["error"] == "not_found"


if __name__ == "__main__":
    unittest.main()
