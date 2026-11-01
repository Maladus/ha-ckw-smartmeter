"""Tests for the local mock CKW API and its fake Keycloak.

``pytest-aiohttp`` is not a dependency of this repository, so the tests drive
``aiohttp.test_utils.TestServer``/``TestClient`` through ``IsolatedAsyncioTestCase``.
Everything here is offline: the mock reads the committed fixtures and binds an
ephemeral localhost port.

The assertions mirror ``docs/reference/ckw-openenergy-api-verification.md``, which
records what the live CKW API and its deployed model actually answer.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.dev.mock import build_app

AUTH: Final[dict[str, str]] = {"Authorization": "Bearer mock-token"}
BAD_AUTH: Final[dict[str, str]] = {"Authorization": "Bearer expired"}
CUSTOMER_URL: Final[str] = "/api/v1/smartmeter/customer"
MEASUREMENT_URL: Final[str] = "/api/v1/smartmeter/measurement"
AUTHORIZE_URL: Final[str] = "/realms/CKW-prod/protocol/openid-connect/auth"
TOKEN_URL: Final[str] = "/realms/CKW-prod/protocol/openid-connect/token"
OTP_URL: Final[str] = "/realms/CKW-prod/otp/login"
FIXTURE_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"
STAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"
ZURICH: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")


def _local_month(stamp: str) -> tuple[int, int]:
    """Return the Europe/Zurich (year, month) a UTC timestamp belongs to."""
    moment = datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)
    local = moment.astimezone(ZURICH)
    return local.year, local.month


class MockApiTest(unittest.IsolatedAsyncioTestCase):
    """One mock instance per test, so in-memory IdP state never leaks."""

    client: TestClient

    async def asyncSetUp(self) -> None:
        self.client = TestClient(TestServer(build_app()))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_gate_requires_token_before_routing(self) -> None:
        """Every API path answers 400 with the missing-token body, like the real host."""
        urls = (
            CUSTOMER_URL,
            f"{MEASUREMENT_URL}/20261001/20261002/days",
            "/api/v1/nope",
            "/api/v1",
        )
        for url in urls:
            response = await self.client.get(url)
            assert response.status == 400, url
            assert await response.json() == {"error": "JWT Token is required."}

            response = await self.client.get(
                url, headers={"Authorization": "Basic not-a-bearer"}
            )
            assert response.status == 400, url
            assert await response.json() == {"error": "JWT Token is required."}

        # The measured 400 carries no rate-limit headers; the mock matches that.
        response = await self.client.get("/api/v1")
        assert "x-ratelimit-remaining" not in response.headers

    async def test_bad_token_answers_401_with_the_measured_body(self) -> None:
        """A known path with an invalid or expired token answers 401, not 400."""
        for token in ("expired", "invalid", "EXPIRED"):
            response = await self.client.get(
                CUSTOMER_URL, headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status == 401, token
            assert await response.json() == {
                "error": (
                    "Bad or expired token. To fix, you should re-authenticate the user."
                )
            }
            assert response.headers["x-ratelimit-limit"] == "4"

    async def test_customer_is_nested_under_a_user_array(self) -> None:
        """The deployed shape is ``customer.User[].locations[].meters[]``."""
        response = await self.client.get(CUSTOMER_URL, headers=AUTH)
        assert response.status == 200
        body = await response.json()

        customer = body["customer"]
        # An extra key on the customer object is legal: the shape is open.
        assert customer["customer_number"] == "0000000099"

        users = customer["User"]
        assert len(users) == 1
        user = users[0]
        assert user["name"] == "Max Beispiel"
        assert user["email"] == "max@example.invalid"
        assert user["phone"] == "+41000000000"

        location = user["locations"][0]
        assert location["address"] == {
            "street_address": "Musterweg 1",
            "postal_code": "6000",
            "city": "Luzern",
        }

        meters = location["meters"]
        assert len(meters) == 3
        assert all(len(meter["deliverypointnumber"]) == 33 for meter in meters)
        identities = {
            (meter["model"], meter["type"], meter["fuel"]) for meter in meters
        }
        assert identities == {
            ("SMART", "import", "elec"),
            ("SMART", "export", "elec"),
            ("ANALOGUE", "consumption", "gas"),
        }

        gas = next(meter for meter in meters if meter["fuel"] == "gas")
        assert gas["custom"] == {"meter_type_indicator": "G4 diaphragm gas meter"}

        # An unknown extra key on a meter is legal too and must not break a client.
        electricity = next(meter for meter in meters if meter["type"] == "import")
        assert electricity["last_calibration"] == "2024-03-15"

    async def test_days_return_one_daily_row_per_meter(self) -> None:
        """``days`` slices the daily fixture by local calendar day."""
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20260922/20261031/days", headers=AUTH
        )
        assert response.status == 200
        body = await response.json()
        assert len(body) == 3
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
        assert len(series) == 3
        flagged = [
            entry
            for entries in series.values()
            for entry in entries
            if entry["anzahl_linien_p"] == 2
        ]
        assert len(flagged) == 1
        assert flagged[0]["zeitstempel_von_utc"] == "2026-10-01T22:00:00"
        assert all(
            entry["anzahl_linien_fw"] == 1 and entry["anzahl_linien_fb"] == 1
            for entries in series.values()
            for entry in entries
        )

    async def test_minutes_return_quarter_hour_rows(self) -> None:
        """``minutes`` serves CKW's finest documented resolution: 15 minutes."""
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20261001/20261002/minutes", headers=AUTH
        )
        assert response.status == 200
        body = await response.json()
        assert len(body) == 3
        for series in body:
            measurements = series["measurements"]
            assert len(measurements) == 96
            assert measurements[0]["zeitstempel_von_utc"] == "2026-09-30T22:00:00"
            assert measurements[0]["zeitstempel_bis_utc"] == "2026-09-30T22:15:00"
            assert measurements[-1]["zeitstempel_von_utc"] == "2026-10-01T21:45:00"
            assert measurements[-1]["zeitstempel_bis_utc"] == "2026-10-01T22:00:00"

        # The same range in days is two daily rows, not 192 quarter-hour rows.
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20261001/20261002/days", headers=AUTH
        )
        assert len((await response.json())[0]["measurements"]) == 2

    async def test_months_sum_daily_rows_and_max_the_counters(self) -> None:
        """``months`` sums quantities/costs and takes the largest line counter."""
        document = json.loads(
            (FIXTURE_DIR / "measurements_days.json").read_text(encoding="utf-8")
        )
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20260901/20261031/months", headers=AUTH
        )
        assert response.status == 200
        body = await response.json()
        assert len(body) == 3

        for series in body:
            deliverypointnumber = series["deliverypointnumber"]
            buckets = series["measurements"]
            assert len(buckets) == 2  # September (22-30) and October (1-31)

            grouped: dict[tuple[int, int], list[dict[str, object]]] = {}
            for entry in document["series"][deliverypointnumber]:
                key = _local_month(str(entry["zeitstempel_von_utc"]))
                grouped.setdefault(key, []).append(entry)

            for bucket, key in zip(buckets, sorted(grouped), strict=True):
                daily = grouped[key]
                assert bucket["menge_fakturiert_ht"] == round(
                    sum(float(entry["menge_fakturiert_ht"]) for entry in daily), 1
                )
                assert bucket["betrag_nt"] == round(
                    sum(float(entry["betrag_nt"]) for entry in daily), 2
                )
                assert bucket["anzahl_linien_p"] == max(
                    int(entry["anzahl_linien_p"]) for entry in daily
                )

            if deliverypointnumber == "CH1003601234500000000000000000000":
                # the 2026-10-02 quality flag sits on the import meter
                assert buckets[-1]["anzahl_linien_p"] == 2
                assert buckets[0]["anzahl_linien_p"] == 1

    async def test_range_cap_answers_the_measured_too_big_body(self) -> None:
        """More than 400 days, or ``?range=too-big``, is a 400 too-big response."""
        response = await self.client.get(
            f"{MEASUREMENT_URL}/20260101/20261231/days", headers=AUTH
        )
        assert response.status == 200

        for url in (
            f"{MEASUREMENT_URL}/20240101/20261231/days",
            f"{MEASUREMENT_URL}/20261001/20261002/days?range=too-big",
        ):
            response = await self.client.get(url, headers=AUTH)
            assert response.status == 400, url
            assert await response.json() == {
                "error": (
                    "response too big, please adjust aggregation or duration "
                    "accordingly"
                )
            }

    async def test_unsupported_aggregation_and_bad_dates_return_400(self) -> None:
        """The documented enum is ten values; anything else is rejected."""
        for aggregation in ("hour", "hours", "Day", "day(s)"):
            response = await self.client.get(
                f"{MEASUREMENT_URL}/20260922/20261031/{aggregation}", headers=AUTH
            )
            assert response.status == 400, aggregation
            assert "unsupported aggregation" in (await response.json())["error"]

        response = await self.client.get(
            f"{MEASUREMENT_URL}/notadate/20261031/days", headers=AUTH
        )
        assert response.status == 400

        response = await self.client.get(
            f"{MEASUREMENT_URL}/20260922/20261031/month", headers=AUTH
        )
        assert response.status == 200

    async def test_injected_failures_use_the_measured_error_bodies(self) -> None:
        """Every injectable status answers the body the deployed model documents."""
        expected = {
            "401": (
                401,
                "Bad or expired token. To fix, you should re-authenticate the user.",
            ),
            "403": (403, "The client id validation failed."),
            "408": (408, "Timeout to get customer data was exceeded."),
            "429": (429, "Quota has been exceeded"),
            "quota": (429, "Quota has been exceeded"),
            "503": (503, "Error communicating with JWKS server."),
        }
        for parameter, (status, message) in expected.items():
            response = await self.client.get(
                f"{CUSTOMER_URL}?fail={parameter}", headers=AUTH
            )
            assert response.status == status, parameter
            assert await response.json() == {"error": message}, parameter
            assert response.headers["x-ratelimit-limit"] == "4"

        response = await self.client.get(f"{CUSTOMER_URL}?fail=500", headers=AUTH)
        assert response.status == 500
        assert set((await response.json()).keys()) == {"error"}

        response = await self.client.get(f"{CUSTOMER_URL}?fail=418", headers=AUTH)
        assert response.status == 400

    async def test_rate_limit_remaining_counts_down(self) -> None:
        """The public host allows 4 requests per window; the mock reports the same."""
        first = await self.client.get(CUSTOMER_URL, headers=AUTH)
        second = await self.client.get(CUSTOMER_URL, headers=AUTH)

        assert first.headers["x-ratelimit-limit"] == "4"
        assert first.headers["x-ratelimit-remaining"] == "3"
        assert second.headers["x-ratelimit-remaining"] == "2"
        assert 0 <= int(second.headers["x-ratelimit-reset"]) <= 1000

        response = await self.client.get(
            f"{CUSTOMER_URL}?ratelimit-remaining=0&ratelimit-reset=2000", headers=AUTH
        )
        assert response.headers["x-ratelimit-remaining"] == "0"
        assert response.headers["x-ratelimit-reset"] == "2000"

    async def test_unknown_path_with_token_is_404(self) -> None:
        """With a token the route table decides, so a typo is a 404."""
        response = await self.client.get("/api/v1/nope", headers=AUTH)
        assert response.status == 404
        assert "no such endpoint" in (await response.json())["error"]

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

    async def test_an_expired_mock_jwt_is_rejected(self) -> None:
        """A JWT-shaped token whose ``exp`` has passed counts as expired."""
        header = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
        payload = "eyJleHAiOjEsInN1YiI6Im1vY2stY3VzdG9tZXIifQ"  # {"exp": 1, "sub": ...}
        response = await self.client.get(
            CUSTOMER_URL, headers={"Authorization": f"Bearer {header}.{payload}.sig"}
        )
        assert response.status == 401

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


if __name__ == "__main__":
    unittest.main()
