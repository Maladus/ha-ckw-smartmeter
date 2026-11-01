"""Tests for the typed CKW API client.

Everything here is offline: ``FakeSession`` stands in for
``aiohttp.ClientSession`` and answers with the committed fixtures under
``tests/fixtures/``, rewritten into the shape the live endpoint returns (the
fixtures group their entries by delivery point, the API sends an array). No
socket is opened, so the suite runs with ``pytest-socket`` in its default,
blocking mode.

The assertions follow ``docs/reference/ckw-openenergy-api.md`` and its
verification page: the nested ``customer.User[].locations[].meters[]`` shape,
the documented error bodies, the ten aggregation values and the
``x-ratelimit-*`` headers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import aiohttp
import pytest

from custom_components.ckw import api as api_module
from custom_components.ckw.api import (
    CkwApiError,
    CkwAuthError,
    CkwClient,
    CkwConnectionError,
    CkwCustomer,
    CkwMeasurement,
    CkwMeterMeasurements,
    CkwRangeTooBigError,
    CkwRateLimitError,
    RateLimitInfo,
    mask_deliverypoint,
)
from custom_components.ckw.const import Fuel, MeterModel, MeterType

FIXTURE_DIR: Final[Path] = Path(__file__).parent / "fixtures"
API_SOURCE: Final[str] = Path(api_module.__file__).read_text(encoding="utf-8")

BASE_URL: Final[str] = "https://api.example.invalid/api/v1"
TOKEN: Final[str] = "not-a-real-token-for-tests"
IMPORT_METER: Final[str] = "CH1003601234500000000000000000000"
EXPORT_METER: Final[str] = "CH1003601234500000000000000000001"
GAS_METER: Final[str] = "CH1003601234500000000000000000002"

# The mock and the fixtures group entries by delivery point; the endpoint sends
# an array of {deliverypointnumber, measurements}. Tests apply that one
# translation, so the parser is exercised against the measured contract.
MEASUREMENT_KEY: Final[str] = "measurements"

QUALITY_FLAG_START: Final[datetime] = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes: one response per request, no sockets
# ---------------------------------------------------------------------------


class FakeResponse:
    """The part of ``aiohttp.ClientResponse`` the client touches."""

    def __init__(
        self,
        status: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def text(self) -> str:
        return self._body


class FakeRequest:
    """Async context manager returned by ``FakeSession.get``."""

    def __init__(
        self,
        response: FakeResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._response = response
        self._error = error

    async def __aenter__(self) -> FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def __aexit__(self, *exc_info: object) -> None:
        return None


@dataclass
class SentRequest:
    """One call the client made, so the test can assert the request shape."""

    url: str
    headers: dict[str, str]
    timeout: aiohttp.ClientTimeout | None


class FakeSession:
    """``aiohttp.ClientSession`` stand-in that replays fixed responses."""

    def __init__(
        self,
        *responses: FakeResponse,
        error: BaseException | None = None,
    ) -> None:
        self._responses = list(responses)
        self._error = error
        self.requests: list[SentRequest] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> FakeRequest:
        self.requests.append(SentRequest(url=url, headers=dict(headers or {}), timeout=timeout))
        if self._error is not None:
            return FakeRequest(error=self._error)
        return FakeRequest(response=self._responses.pop(0))


def json_response(
    payload: Any,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> FakeResponse:
    """A JSON response, as both the real API and the mock send it."""
    return FakeResponse(status=status, body=json.dumps(payload), headers=headers)


def make_client(session: FakeSession) -> CkwClient:
    """Build a client whose token provider always returns a test token."""

    async def token_provider() -> str:
        return TOKEN

    return CkwClient(session, token_provider, base_url=BASE_URL)


def load_fixture(name: str) -> Any:
    """Read a committed fixture as JSON."""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def measurement_payload(name: str) -> list[dict[str, Any]]:
    """Rewrite a fixture document into the array the endpoint returns."""
    document = load_fixture(name)
    return [
        {"deliverypointnumber": deliverypoint, MEASUREMENT_KEY: entries}
        for deliverypoint, entries in document["series"].items()
    ]


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------


async def test_customer_parses_the_nested_user_shape() -> None:
    """``customer.User[].locations[].meters[]`` parses into typed dataclasses."""
    payload = load_fixture("customer.json")
    # An extra key at every level is legal: the shape is open.
    payload["customer"]["unexpected_top_level"] = {"added": "without a version bump"}
    payload["customer"]["User"][0]["unexpected_user_key"] = True
    session = FakeSession(json_response(payload))
    client = make_client(session)

    customer = await client.async_get_customer()

    assert isinstance(customer, CkwCustomer)
    assert customer.id == "0000000099"
    assert len(customer.locations) == 1

    location = customer.locations[0]
    assert (location.street, location.postal_code, location.city) == (
        "Musterweg 1",
        "6000",
        "Luzern",
    )
    # The user's email and phone are never carried into the parsed model.
    assert not hasattr(location, "email")

    meters = location.meters
    assert len(meters) == 3
    assert [meter.deliverypointnumber for meter in meters] == [
        IMPORT_METER,
        EXPORT_METER,
        GAS_METER,
    ]

    # Two SMART meters of different ``type``, plus an analogue gas meter.
    import_meter, export_meter, gas_meter = meters
    assert import_meter.model is MeterModel.SMART
    assert import_meter.type is MeterType.IMPORT
    assert import_meter.fuel is Fuel.ELEC
    assert export_meter.type is MeterType.EXPORT
    assert gas_meter.model is MeterModel.ANALOGUE
    assert gas_meter.type is MeterType.CONSUMPTION
    assert gas_meter.fuel is Fuel.GAS
    assert len({import_meter.type, export_meter.type, gas_meter.type}) == 3

    assert not import_meter.is_feed_in
    assert export_meter.is_feed_in
    assert import_meter.supply_start == "2019-04-01"
    # An empty supply end means "still running", not the empty string.
    assert import_meter.supply_end is None


async def test_customer_tolerates_missing_keys() -> None:
    """A payload without the wrapper, users or meters parses to empty values."""
    session = FakeSession(json_response({}))
    customer = await make_client(session).async_get_customer()

    assert customer.id == ""
    assert customer.locations == ()


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


async def test_daily_measurements_parse_values_and_derived_totals() -> None:
    """The daily fixture parses into intervals, totals and one quality flag."""
    session = FakeSession(json_response(measurement_payload("measurements_days.json")))
    client = make_client(session)

    series = await client.async_get_measurements(date(2026, 9, 22), date(2026, 10, 31))

    assert len(series) == 3
    assert {item.deliverypointnumber for item in series} == {
        IMPORT_METER,
        EXPORT_METER,
        GAS_METER,
    }
    assert all(isinstance(item, CkwMeterMeasurements) for item in series)

    by_delivery_point = {item.deliverypointnumber: item.measurements for item in series}
    intervals = by_delivery_point[IMPORT_METER]
    assert len(intervals) == 40

    first = intervals[0]
    assert isinstance(first, CkwMeasurement)
    assert first.start_utc == datetime(2026, 9, 21, 22, 0, tzinfo=UTC)
    assert first.end_utc == datetime(2026, 9, 22, 22, 0, tzinfo=UTC)
    assert first.kwh_ht == pytest.approx(14.5)
    assert first.kwh_nt == pytest.approx(9.1)
    assert first.kwh_total == pytest.approx(23.6)
    assert first.chf_ht == pytest.approx(3.48)
    assert first.chf_nt == pytest.approx(1.73)
    assert first.chf_total == pytest.approx(5.21)
    assert (first.lines_p, first.lines_fw, first.lines_fb) == (1, 1, 1)
    assert not first.has_quality_issue

    flagged = [interval for interval in intervals if interval.has_quality_issue]
    assert len(flagged) == 1
    assert flagged[0].start_utc == QUALITY_FLAG_START
    assert flagged[0].lines_p == 2

    # The same single flag across all meters: fw/fb are 1 normally, not 0.
    every_interval = [entry for entries in by_delivery_point.values() for entry in entries]
    assert sum(1 for entry in every_interval if entry.has_quality_issue) == 1
    assert all(entry.lines_fw == 1 and entry.lines_fb == 1 for entry in every_interval)


async def test_quarter_hour_measurements_parse_fifteen_minute_intervals() -> None:
    """``minutes`` is CKW's finest resolution: 96 quarter-hour rows per day."""
    session = FakeSession(json_response(measurement_payload("measurements_minutes.json")))
    client = make_client(session)

    series = await client.async_get_measurements(date(2026, 10, 1), date(2026, 10, 2), "minutes")

    assert len(series) == 3
    for item in series:
        intervals = item.measurements
        assert len(intervals) == 96
        assert intervals[0].start_utc == datetime(2026, 9, 30, 22, 0, tzinfo=UTC)
        assert intervals[0].end_utc == datetime(2026, 9, 30, 22, 15, tzinfo=UTC)
        assert intervals[-1].end_utc == datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
        assert all(
            interval.end_utc is not None
            and interval.start_utc is not None
            and interval.end_utc - interval.start_utc == timedelta(minutes=15)
            for interval in intervals
        )
        assert not any(interval.has_quality_issue for interval in intervals)

        totals = [interval.kwh_total for interval in intervals]
        assert all(total is not None for total in totals)
        expected = sum(
            (interval.kwh_ht or 0.0) + (interval.kwh_nt or 0.0) for interval in intervals
        )
        assert sum(total or 0.0 for total in totals) == pytest.approx(expected)


async def test_missing_measurement_fields_become_none() -> None:
    """A missing number is ``None`` and never a fabricated ``0``."""
    payload = [
        {
            "deliverypointnumber": IMPORT_METER,
            MEASUREMENT_KEY: [
                {
                    "zeitstempel_von_utc": "2026-10-01T00:00:00",
                    "zeitstempel_bis_utc": "2026-10-01T00:15:00",
                    "menge_fakturiert_ht": 1.5,
                },
                {
                    "zeitstempel_von_utc": "2026-10-01T00:15:00",
                    "zeitstempel_bis_utc": "2026-10-01T00:30:00",
                },
            ],
        }
    ]
    session = FakeSession(json_response(payload))
    series = await make_client(session).async_get_measurements(
        date(2026, 10, 1), date(2026, 10, 1), "minutes"
    )

    partial, empty = series[0].measurements
    assert partial.kwh_ht == pytest.approx(1.5)
    assert partial.kwh_nt is None
    assert partial.kwh_total is None
    assert partial.chf_ht is None
    assert partial.chf_nt is None
    assert partial.chf_total is None
    assert (partial.lines_p, partial.lines_fw, partial.lines_fb) == (None, None, None)
    assert not partial.has_quality_issue
    assert partial.start_utc == datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

    assert empty.start_utc == datetime(2026, 10, 1, 0, 15, tzinfo=UTC)
    assert empty.kwh_total is None
    assert empty.chf_total is None
    assert not empty.has_quality_issue


async def test_measurement_response_tolerates_odd_elements() -> None:
    """A non-object element is skipped; an empty object is an empty interval."""
    payload: list[Any] = [
        None,
        7,
        {"deliverypointnumber": IMPORT_METER, MEASUREMENT_KEY: [None, {}]},
        {"unexpected": "no delivery point and no measurements"},
    ]
    session = FakeSession(json_response(payload))
    series = await make_client(session).async_get_measurements(date(2026, 10, 1), date(2026, 10, 1))

    assert len(series) == 2
    assert len(series[0].measurements) == 1
    assert series[0].measurements[0].kwh_total is None
    assert series[1].deliverypointnumber == ""
    assert series[1].measurements == ()


async def test_an_empty_measurement_array_is_an_empty_list() -> None:
    """A range without data answers an empty array, not an error."""
    session = FakeSession(json_response([]))
    series = await make_client(session).async_get_measurements(date(2026, 10, 1), date(2026, 10, 1))
    assert series == []


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


async def test_requests_carry_the_token_a_timeout_and_the_customer_path() -> None:
    """The session and token come from outside; the timeout is 30 s total."""
    session = FakeSession(json_response({"customer": {"User": []}}))
    client = make_client(session)

    await client.async_get_customer()

    request = session.requests[0]
    assert request.url == f"{BASE_URL}/smartmeter/customer"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/json"
    assert request.timeout is not None
    assert request.timeout.total == 30


async def test_measurement_url_building_for_days_and_minutes() -> None:
    """Dates become ``YYYYMMDD`` path segments and the aggregation is verbatim."""
    session = FakeSession(json_response([]), json_response([]))
    client = make_client(session)

    await client.async_get_measurements(date(2026, 9, 22), date(2026, 10, 31))
    await client.async_get_measurements(date(2026, 10, 1), date(2026, 10, 2), aggregation="minutes")

    assert session.requests[0].url == (f"{BASE_URL}/smartmeter/measurement/20260922/20261031/days")
    assert session.requests[1].url == (
        f"{BASE_URL}/smartmeter/measurement/20261001/20261002/minutes"
    )


async def test_a_trailing_slash_in_the_base_url_is_harmless() -> None:
    """The override may carry a trailing slash; paths stay single-slashed."""
    session = FakeSession(json_response({"customer": {"User": []}}))

    async def token_provider() -> str:
        return TOKEN

    client = CkwClient(session, token_provider, base_url=f"{BASE_URL}/")
    await client.async_get_customer()

    assert session.requests[0].url == f"{BASE_URL}/smartmeter/customer"


async def test_an_unknown_aggregation_is_rejected_before_a_request() -> None:
    """Only the ten documented values are sent, so no quota is wasted on a typo."""
    session = FakeSession()
    client = make_client(session)

    with pytest.raises(ValueError, match="unsupported aggregation"):
        await client.async_get_measurements(date(2026, 10, 1), date(2026, 10, 2), "day(s)")

    assert session.requests == []


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (400, "JWT Token is required.", CkwAuthError),
        (400, "Token was not provided.", CkwAuthError),
        (
            400,
            "response too big, please adjust aggregation or duration accordingly",
            CkwRangeTooBigError,
        ),
        (
            401,
            "Bad or expired token. To fix, you should re-authenticate the user.",
            CkwAuthError,
        ),
        (403, "The client id validation failed.", CkwAuthError),
        (408, "Timeout to get customer data was exceeded.", CkwApiError),
        (500, "An internal error has occurred.", CkwApiError),
        (503, "Error communicating with JWKS server.", CkwApiError),
    ],
)
async def test_documented_error_bodies_map_to_typed_exceptions(
    status: int, message: str, expected: type[Exception]
) -> None:
    """Every body from the verification page maps to its documented exception."""
    session = FakeSession(
        json_response(
            {"error": message},
            status=status,
            headers={
                "x-ratelimit-limit": "4",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "2000",
            },
        )
    )
    client = make_client(session)

    with pytest.raises(expected) as excinfo:
        await client.async_get_customer()

    assert str(excinfo.value) == message
    if isinstance(excinfo.value, CkwApiError):
        assert excinfo.value.status == status
        assert message in excinfo.value.body
    # The headers of a failing response are reported too.
    assert client.last_rate_limit == RateLimitInfo(limit=4, remaining=0, reset_ms=2000)


async def test_a_plain_text_error_body_is_kept_as_the_message() -> None:
    """A proxy may answer HTML or text; the message is still readable."""
    session = FakeSession(FakeResponse(status=502, body="Bad Gateway\n"))
    client = make_client(session)

    with pytest.raises(CkwApiError) as excinfo:
        await client.async_get_customer()

    assert excinfo.value.status == 502
    assert str(excinfo.value) == "Bad Gateway"


async def test_an_unparseable_success_body_is_an_api_error() -> None:
    """A 200 that is not JSON must not escape as ``JSONDecodeError``."""
    session = FakeSession(FakeResponse(status=200, body="<html>maintenance</html>"))
    client = make_client(session)

    with pytest.raises(CkwApiError) as excinfo:
        await client.async_get_customer()

    assert excinfo.value.status == 200
    assert "invalid JSON" in str(excinfo.value)


async def test_rate_limit_error_carries_reset_seconds_and_remaining() -> None:
    """A 429 reports the window from ``x-ratelimit-reset`` (ms) and the quota."""
    session = FakeSession(
        json_response(
            {"error": "Quota has been exceeded"},
            status=429,
            headers={
                "X-RateLimit-Limit": "4",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "2000",
            },
        )
    )
    client = make_client(session)

    with pytest.raises(CkwRateLimitError) as excinfo:
        await client.async_get_customer()

    error = excinfo.value
    assert str(error) == "Quota has been exceeded"
    assert error.retry_after_seconds == pytest.approx(2.0)
    assert error.remaining == 0
    assert client.last_rate_limit is not None
    assert client.last_rate_limit.is_exhausted
    assert client.last_rate_limit.reset_seconds == pytest.approx(2.0)
    assert TOKEN not in str(error)


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientConnectionError("cannot connect"),
        TimeoutError("the request took too long"),
    ],
)
async def test_transport_failures_become_connection_errors(
    error: BaseException,
) -> None:
    """Connection failures and timeouts are one typed error, wrapped, no leak."""
    client = make_client(FakeSession(error=error))

    with pytest.raises(CkwConnectionError) as excinfo:
        await client.async_get_customer()

    assert excinfo.value.__cause__ is error
    assert TOKEN not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_rate_limit_headers_are_parsed_from_every_response() -> None:
    """``last_rate_limit`` refreshes on each response, ``remaining: 0`` included."""
    session = FakeSession(
        json_response(
            {"customer": {"User": []}},
            headers={
                "x-ratelimit-limit": "4",
                "x-ratelimit-remaining": "3",
                "x-ratelimit-reset": "998",
            },
        ),
        json_response(
            {"customer": {"User": []}},
            headers={
                "x-ratelimit-limit": "4",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "2000",
            },
        ),
    )
    client = make_client(session)

    await client.async_get_customer()
    assert client.last_rate_limit == RateLimitInfo(limit=4, remaining=3, reset_ms=998)
    assert client.last_rate_limit is not None
    assert client.last_rate_limit.reset_seconds == pytest.approx(0.998)
    assert not client.last_rate_limit.is_exhausted

    await client.async_get_customer()
    assert client.last_rate_limit == RateLimitInfo(limit=4, remaining=0, reset_ms=2000)
    assert client.last_rate_limit is not None
    assert client.last_rate_limit.is_exhausted


async def test_missing_rate_limit_headers_stay_none() -> None:
    """The meter host may send no headers at all; that is not "no quota"."""
    session = FakeSession(json_response({"customer": {"User": []}}))
    client = make_client(session)

    await client.async_get_customer()

    assert client.last_rate_limit == RateLimitInfo()
    assert client.last_rate_limit is not None
    assert client.last_rate_limit.reset_ms is None
    assert client.last_rate_limit.reset_seconds is None
    assert not client.last_rate_limit.is_exhausted


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


async def test_an_unknown_enum_value_is_kept_and_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A value CKW adds without a version bump stays readable, logged once."""
    caplog.set_level(logging.DEBUG, logger="custom_components.ckw.api")
    meters = [
        {
            "id": str(index),
            "model": "e450-obsolete",
            "type": "electricity",
            "fuel": "electricity",
            "deliverypointnumber": f"{IMPORT_METER}{index}",
        }
        for index in range(2)
    ]
    payload = {"customer": {"User": [{"id": "1", "locations": [{"meters": meters}]}]}}
    session = FakeSession(json_response(payload))

    customer = await make_client(session).async_get_customer()

    parsed = customer.locations[0].meters
    assert [meter.model for meter in parsed] == ["e450-obsolete", "e450-obsolete"]
    assert [meter.type for meter in parsed] == ["electricity", "electricity"]
    assert [meter.fuel for meter in parsed] == ["electricity", "electricity"]

    model_logs = [
        record for record in caplog.records if "unknown meter model" in record.getMessage()
    ]
    assert len(model_logs) == 1
    assert model_logs[0].levelno == logging.DEBUG


# ---------------------------------------------------------------------------
# Redaction and the Home-Assistant-free boundary
# ---------------------------------------------------------------------------


def test_mask_deliverypoint_shows_only_the_last_four_characters() -> None:
    """A Zählpunkt appears in logs as its last four characters and nothing else."""
    masked = mask_deliverypoint(IMPORT_METER)

    assert len(masked) == len(IMPORT_METER) == 33
    assert masked.endswith("0000")
    assert masked[:-4] == "*" * 29
    assert masked != IMPORT_METER


def test_mask_deliverypoint_masks_short_values_entirely() -> None:
    """There are no "last four" characters in a short value, so none show."""
    assert mask_deliverypoint("CH") == "**"
    assert mask_deliverypoint("") == ""
    assert mask_deliverypoint("ABCD") == "****"


def test_api_module_does_not_import_home_assistant() -> None:
    """The client boundary (``PLAN.md`` #138) holds: no HA import, no HA name."""
    assert "homeassistant" not in API_SOURCE


async def test_numeric_drift_is_tolerated_but_never_invented() -> None:
    """A numeric string is read; a non-number, a NaN and a bool are missing."""
    payload = [
        {
            "deliverypointnumber": IMPORT_METER,
            MEASUREMENT_KEY: [
                {
                    "zeitstempel_von_utc": "2026-10-01T00:00:00",
                    "zeitstempel_bis_utc": "2026-10-01T00:15:00",
                    "menge_fakturiert_ht": "2.5",
                    "menge_fakturiert_nt": "n/a",
                    "betrag_ht": "NaN",
                    "betrag_nt": True,
                    "anzahl_linien_p": "1.0",
                    "anzahl_linien_fw": 1.5,
                    "anzahl_linien_fb": "3",
                }
            ],
        }
    ]
    session = FakeSession(json_response(payload))
    series = await make_client(session).async_get_measurements(
        date(2026, 10, 1), date(2026, 10, 1), "minutes"
    )

    interval = series[0].measurements[0]
    assert interval.kwh_ht == pytest.approx(2.5)
    assert interval.kwh_nt is None
    assert interval.kwh_total is None
    assert interval.chf_ht is None
    assert interval.chf_nt is None
    assert interval.lines_p == 1
    assert interval.lines_fw is None
    assert interval.lines_fb == 3
    assert interval.has_quality_issue
