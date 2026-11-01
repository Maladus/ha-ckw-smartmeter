"""Typed client for the CKW OpenEnergy API, with no Home Assistant imports.

This module is the only place that speaks HTTP to CKW. It owns the request
shapes, the parsing of the two payloads we consume and the mapping from an HTTP
status to a typed exception; it owns neither the session nor the token. Both are
injected, so the same client runs inside Home Assistant and inside the tests
(``docs/reference/conventions.md`` "Error mapping", ``PLAN.md`` #138).

Three rules shape everything below:

* **The response shapes are open** (``shacl:closed: false``). Unknown keys are
  ignored, missing keys are tolerated, and a number that is absent becomes
  ``None`` - never a fabricated ``0`` that would silently look like a reading.
* **An unknown enum value stays the raw string.** CKW may add a meter ``type``
  without a version bump; dropping the value would turn a new meter into a
  broken one, so it is kept and logged once at debug.
* **Nothing secret is logged.** The token is read through the provider, never
  stored, and a delivery point number only ever appears as its last four
  characters (``mask_deliverypoint``).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Final

import aiohttp

from .const import AGGREGATIONS, API_BASE_URL, Fuel, MeterModel, MeterType

__all__ = [
    "CkwApiError",
    "CkwAuthError",
    "CkwClient",
    "CkwConnectionError",
    "CkwCustomer",
    "CkwError",
    "CkwLocation",
    "CkwMeasurement",
    "CkwMeter",
    "CkwMeterMeasurements",
    "CkwRangeTooBigError",
    "CkwRateLimitError",
    "RateLimitInfo",
    "mask_deliverypoint",
]

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: Total budget of one request, connection included. No retries happen in this
#: module: pacing and retry belong to the coordinator (``PLAN.md`` #147).
REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0

CUSTOMER_PATH: Final[str] = "/smartmeter/customer"
MEASUREMENT_PATH: Final[str] = "/smartmeter/measurement/{von}/{bis}/{aggregation}"
DATE_FORMAT: Final[str] = "%Y%m%d"

HEADER_RATE_LIMIT_LIMIT: Final[str] = "x-ratelimit-limit"
HEADER_RATE_LIMIT_REMAINING: Final[str] = "x-ratelimit-remaining"
HEADER_RATE_LIMIT_RESET: Final[str] = "x-ratelimit-reset"

#: Case-insensitive markers of the two 400 bodies that mean "no usable token".
MISSING_TOKEN_MARKERS: Final[tuple[str, ...]] = (
    "jwt token is required",
    "token was not provided",
)

#: Case-insensitive marker of the measurement endpoint's range cap.
RANGE_TOO_BIG_MARKER: Final[str] = "response too big"

VISIBLE_DELIVERYPOINT_CHARS: Final[int] = 4

#: ``(field, value)`` pairs already reported, so a flood of one unknown enum is
#: logged once per process instead of once per meter and poll.
_LOGGED_UNKNOWN_ENUMS: Final[set[tuple[str, str]]] = set()


# ---------------------------------------------------------------------------
# Errors (mapped to Home Assistant exceptions by coordinator.py, never here)
# ---------------------------------------------------------------------------


class CkwError(Exception):
    """Base class of every failure this module raises on purpose."""


class CkwConnectionError(CkwError):
    """The request never produced a response: connection failure or timeout."""


class CkwAuthError(CkwError):
    """The token is missing, invalid or expired, or the client is not allowed."""


class CkwApiError(CkwError):
    """The API answered with a status this module does not handle specially."""

    def __init__(self, status: int, body: str) -> None:
        message = _message_from_body(body) or f"HTTP {status}"
        super().__init__(message)
        self.status = status
        self.body = body


class CkwRangeTooBigError(CkwApiError):
    """``400`` "response too big": the requested range exceeds the server cap."""


class CkwRateLimitError(CkwError):
    """``429`` - the quota is exhausted; wait for the next window, do not fail."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        remaining: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.remaining = remaining


# ---------------------------------------------------------------------------
# Rate limiting (PLAN.md #147 reads this, this module only reports it)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    """The ``x-ratelimit-*`` headers of one response.

    ``reset_ms`` is milliseconds until a new window starts, exactly as CKW
    sends it; ``reset_seconds`` is the same value for callers that sleep.
    A missing header stays ``None``, so "not reported" is never mistaken for
    "no quota left".
    """

    limit: int | None = None
    remaining: int | None = None
    reset_ms: int | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> RateLimitInfo:
        """Read the three headers, tolerating any of them being absent."""
        return cls(
            limit=_header_int(headers, HEADER_RATE_LIMIT_LIMIT),
            remaining=_header_int(headers, HEADER_RATE_LIMIT_REMAINING),
            reset_ms=_header_int(headers, HEADER_RATE_LIMIT_RESET),
        )

    @property
    def reset_seconds(self) -> float | None:
        """Milliseconds until the next window, in seconds."""
        if self.reset_ms is None:
            return None
        return self.reset_ms / 1000

    @property
    def is_exhausted(self) -> bool:
        """Whether the response says no request is left in this window."""
        return self.remaining == 0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CkwMeter:
    """One meter of one location, as ``customer.User[].locations[].meters[]``.

    ``model``, ``type`` and ``fuel`` hold the matching ``const`` enum when CKW
    sends a documented value and the raw string when it sends a new one.
    """

    id: str | None
    model: MeterModel | str | None
    type: MeterType | str | None
    fuel: Fuel | str | None
    deliverypointnumber: str
    supply_start: str | None
    supply_end: str | None

    @property
    def is_feed_in(self) -> bool:
        """Whether this meter reports production rather than consumption."""
        return self.type in (MeterType.EXPORT, MeterType.PRODUCTION)


@dataclass(frozen=True, slots=True)
class CkwLocation:
    """One location (delivery address) with the meters installed there."""

    id: str | None
    gpnr: str | None
    street: str | None
    postal_code: str | None
    city: str | None
    meters: tuple[CkwMeter, ...]


@dataclass(frozen=True, slots=True)
class CkwCustomer:
    """The account and every location of every user mapped to it.

    ``id`` is the customer number when the payload carries one, else the first
    user id. Several users can share locations, so locations are not deduplicated
    here; consumers key meters by ``deliverypointnumber``.
    """

    id: str
    locations: tuple[CkwLocation, ...]


@dataclass(frozen=True, slots=True)
class CkwMeasurement:
    """One measurement interval of one delivery point.

    Every quantity is optional: a missing field is ``None`` and never ``0``.
    The API sends UTC ``dateTimeOnly`` timestamps, parsed to aware UTC here.
    """

    start_utc: datetime | None
    end_utc: datetime | None
    kwh_ht: float | None
    kwh_nt: float | None
    chf_ht: float | None
    chf_nt: float | None
    lines_p: int | None
    lines_fw: int | None
    lines_fb: int | None

    @property
    def kwh_total(self) -> float | None:
        """Billed quantity, or ``None`` when either tariff half is missing."""
        if self.kwh_ht is None or self.kwh_nt is None:
            return None
        return self.kwh_ht + self.kwh_nt

    @property
    def chf_total(self) -> float | None:
        """Base price without VAT and surcharges, or ``None`` if incomplete."""
        if self.chf_ht is None or self.chf_nt is None:
            return None
        return self.chf_ht + self.chf_nt

    @property
    def has_quality_issue(self) -> bool:
        """Whether any timeseries counter is above ``1`` (``1`` is normal)."""
        return any(
            count is not None and count > 1
            for count in (self.lines_p, self.lines_fw, self.lines_fb)
        )


@dataclass(frozen=True, slots=True)
class CkwMeterMeasurements:
    """The measurements the API returned for one delivery point."""

    deliverypointnumber: str
    measurements: tuple[CkwMeasurement, ...]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def mask_deliverypoint(deliverypointnumber: str) -> str:
    """Mask all but the last four characters of a Zählpunkt, for logs.

    A short value is masked entirely rather than left readable, because the
    tail of a truncated Zählpunkt is not the four characters that identify it.
    """
    if len(deliverypointnumber) <= VISIBLE_DELIVERYPOINT_CHARS:
        return "*" * len(deliverypointnumber)
    hidden = len(deliverypointnumber) - VISIBLE_DELIVERYPOINT_CHARS
    return "*" * hidden + deliverypointnumber[-VISIBLE_DELIVERYPOINT_CHARS:]


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class CkwClient:
    """Async client for the two OpenEnergy endpoints this integration uses.

    The session and the token come from outside: the coordinator injects
    Home Assistant's shared session, and the token provider hides the whole
    OAuth refresh dance. One client instance is one account; ``last_rate_limit``
    holds the headers of the most recent response so the coordinator can pace.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: Callable[[], Awaitable[str]],
        base_url: str = API_BASE_URL,
    ) -> None:
        self._session = session
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        self.last_rate_limit: RateLimitInfo | None = None

    async def async_get_customer(self) -> CkwCustomer:
        """Return the account, its locations and their meters."""
        payload = await self._async_get_json(CUSTOMER_PATH)
        return _parse_customer(payload)

    async def async_get_measurements(
        self,
        start: date,
        end: date,
        aggregation: str = "days",
    ) -> list[CkwMeterMeasurements]:
        """Return the measurements of every delivery point in ``start..end``.

        ``aggregation`` is one of the ten documented values; anything else is
        rejected before a request is spent on it. Dates are formatted as
        ``YYYYMMDD`` from the ``date`` given, so the caller decides the zone.
        """
        if aggregation not in AGGREGATIONS:
            raise ValueError(
                f"unsupported aggregation {aggregation!r}, "
                f"expected one of {', '.join(AGGREGATIONS)}"
            )
        path = MEASUREMENT_PATH.format(
            von=start.strftime(DATE_FORMAT),
            bis=end.strftime(DATE_FORMAT),
            aggregation=aggregation,
        )
        payload = await self._async_get_json(path)
        series = _parse_measurements(payload)
        _LOGGER.debug(
            "received %d intervals for %s",
            sum(len(item.measurements) for item in series),
            ", ".join(mask_deliverypoint(item.deliverypointnumber) for item in series),
        )
        return series

    async def _async_get_json(self, path: str) -> Any:
        """GET ``path`` and return the decoded body, or raise a typed error."""
        token = await self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        _LOGGER.debug("GET %s", path)
        # A request without a response has no headers; never report a stale window.
        self.last_rate_limit = None

        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=headers,
                timeout=self._timeout,
            ) as response:
                self.last_rate_limit = RateLimitInfo.from_headers(response.headers)
                status = response.status
                body = await response.text()
        except TimeoutError as err:
            raise CkwConnectionError(
                f"request to {path} timed out after {REQUEST_TIMEOUT_SECONDS:g} s"
            ) from err
        except aiohttp.ClientError as err:
            raise CkwConnectionError(f"request to {path} failed: {err}") from err

        if not 200 <= status < 300:
            raise _error_for(status, body, self.last_rate_limit)
        return _decode_json(status, body)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_customer(payload: object) -> CkwCustomer:
    """Build a ``CkwCustomer`` from ``customer.User[].locations[].meters[]``."""
    root = _as_mapping(payload)
    customer = _as_mapping(root.get("customer")) or root
    users = _as_list(customer.get("User"))

    locations: list[CkwLocation] = []
    for user in users:
        locations.extend(
            _parse_location(raw) for raw in _as_list(_as_mapping(user).get("locations"))
        )

    customer_id = _as_text(customer.get("customer_number"))
    if customer_id is None:
        for user in users:
            customer_id = _as_text(_as_mapping(user).get("id"))
            if customer_id is not None:
                break

    return CkwCustomer(id=customer_id or "", locations=tuple(locations))


def _parse_location(value: object) -> CkwLocation:
    """Build a ``CkwLocation``, flattening the nested address object."""
    location = _as_mapping(value)
    address = _as_mapping(location.get("address"))
    return CkwLocation(
        id=_as_text(location.get("id")),
        gpnr=_as_text(location.get("gpnr")),
        street=_as_text(address.get("street_address")),
        postal_code=_as_text(address.get("postal_code")),
        city=_as_text(address.get("city")),
        meters=tuple(_parse_meter(raw) for raw in _as_list(location.get("meters"))),
    )


def _parse_meter(value: object) -> CkwMeter:
    """Build a ``CkwMeter``; ``custom`` and any newer key are ignored."""
    meter = _as_mapping(value)
    return CkwMeter(
        id=_as_text(meter.get("id")),
        model=_coerce_enum(meter.get("model"), MeterModel, field="meter model"),
        type=_coerce_enum(meter.get("type"), MeterType, field="meter type"),
        fuel=_coerce_enum(meter.get("fuel"), Fuel, field="meter fuel"),
        deliverypointnumber=_as_text(meter.get("deliverypointnumber")) or "",
        supply_start=_as_text(meter.get("supply_start_date")),
        supply_end=_as_text(meter.get("supply_end_date")),
    )


def _parse_measurements(payload: object) -> list[CkwMeterMeasurements]:
    """Build the per-delivery-point list the endpoint returns."""
    series: list[CkwMeterMeasurements] = []
    for item in _as_list(payload):
        if not isinstance(item, Mapping):
            _LOGGER.debug("skipping non-object measurement series %r", item)
            continue
        entry = _as_mapping(item)
        series.append(
            CkwMeterMeasurements(
                deliverypointnumber=_as_text(entry.get("deliverypointnumber")) or "",
                measurements=tuple(
                    _parse_measurement(raw)
                    for raw in _as_list(entry.get("measurements"))
                    if isinstance(raw, Mapping)
                ),
            )
        )
    return series


def _parse_measurement(value: object) -> CkwMeasurement:
    """Build one interval, with ``None`` for every field that is absent."""
    measurement = _as_mapping(value)
    return CkwMeasurement(
        start_utc=_as_utc_datetime(measurement.get("zeitstempel_von_utc")),
        end_utc=_as_utc_datetime(measurement.get("zeitstempel_bis_utc")),
        kwh_ht=_as_number(measurement.get("menge_fakturiert_ht")),
        kwh_nt=_as_number(measurement.get("menge_fakturiert_nt")),
        chf_ht=_as_number(measurement.get("betrag_ht")),
        chf_nt=_as_number(measurement.get("betrag_nt")),
        lines_p=_as_int(measurement.get("anzahl_linien_p")),
        lines_fw=_as_int(measurement.get("anzahl_linien_fw")),
        lines_fb=_as_int(measurement.get("anzahl_linien_fb")),
    )


def _coerce_enum[EnumT: StrEnum](
    value: object, enum_type: type[EnumT], *, field: str
) -> EnumT | str | None:
    """Return the enum member, or the raw string when CKW sends a new value."""
    if value is None:
        return None
    if not isinstance(value, str):
        _LOGGER.debug("ignoring non-string %s %r", field, value)
        return None
    try:
        return enum_type(value)
    except ValueError:
        key = (field, value)
        if key not in _LOGGED_UNKNOWN_ENUMS:
            _LOGGED_UNKNOWN_ENUMS.add(key)
            _LOGGER.debug("unknown %s %r, keeping the raw string", field, value)
        return value


def _as_mapping(value: object) -> Mapping[str, Any]:
    """Return ``value`` as a string-keyed mapping, or an empty one."""
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list(value: object) -> list[Any]:
    """Return ``value`` as a list, or an empty one."""
    if isinstance(value, list):
        return value
    return []


def _as_text(value: object) -> str | None:
    """Return a non-empty trimmed string, or ``None``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_number(value: object) -> float | None:
    """Return a finite float, or ``None`` for anything that is not a number."""
    if isinstance(value, bool):
        return None
    number: float | None = None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    if number is None or not math.isfinite(number):
        return None
    return number


def _as_int(value: object) -> int | None:
    """Return an integer counter, or ``None``; ``1.0`` counts as ``1``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            pass
        number = _as_number(text)
        if number is None or not number.is_integer():
            return None
        return int(number)
    return None


def _as_utc_datetime(value: object) -> datetime | None:
    """Parse a ``dateTimeOnly`` stamp as UTC; tolerate a ``Z`` or an offset."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        _LOGGER.debug("cannot parse timestamp %r, treating it as missing", value)
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Read a header case-insensitively, for plain dicts and aiohttp alike."""
    value = headers.get(name)
    if value is not None:
        return value
    wanted = name.lower()
    for key, candidate in headers.items():
        if key.lower() == wanted:
            return candidate
    return None


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    """Read a non-negative integer header, or ``None``."""
    raw = _header(headers, name)
    if raw is None:
        return None
    try:
        return max(int(raw.strip()), 0)
    except ValueError:
        return None


def _message_from_body(body: str) -> str | None:
    """Extract the human message from a JSON or plain-text error body."""
    text = body.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
    return text


def _decode_json(status: int, body: str) -> Any:
    """Decode a successful body, or fail with the status and a short excerpt."""
    text = body.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise CkwApiError(status, f"invalid JSON response: {text[:200]}") from err


def _error_for(status: int, body: str, rate_limit: RateLimitInfo | None) -> CkwError:
    """Map an HTTP failure to the exception ``conventions.md`` documents."""
    message = _message_from_body(body) or f"HTTP {status}"
    lowered = message.lower()

    if status in (401, 403):
        return CkwAuthError(message)
    if status == 400:
        if RANGE_TOO_BIG_MARKER in lowered:
            return CkwRangeTooBigError(status, body)
        if any(marker in lowered for marker in MISSING_TOKEN_MARKERS):
            # The live gate answers 400, not 401, when no token is sent.
            return CkwAuthError(message)
        return CkwApiError(status, body)
    if status == 429:
        return CkwRateLimitError(
            message,
            retry_after_seconds=None if rate_limit is None else rate_limit.reset_seconds,
            remaining=None if rate_limit is None else rate_limit.remaining,
        )
    return CkwApiError(status, body)
