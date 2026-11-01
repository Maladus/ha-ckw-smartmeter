"""Local mock of the CKW OpenEnergy API.

This is a *mock*: it answers with the committed fixtures under ``tests/fixtures/``
and never talks to CKW. It imports nothing from Home Assistant and needs no
network, which is what makes offline integration work possible (#39, #141).

Run it with::

    mise run dev-mock
    mise exec -- python -m scripts.dev.mock --port 8787
    mise exec -- python scripts/dev/mock/app.py --port 8787

The three API endpoints live under ``/api/v1``; the fake Keycloak lives under
``/realms`` (see ``keycloak.py``) and is registered by ``build_app``.

Behaviour follows ``docs/reference/ckw-openenergy-api-verification.md`` where that
page has measurements: the ``400 {"error": "JWT Token is required."}`` gate runs
before routing, a bad token answers ``401``, the documented error bodies are used
verbatim, ``aggregation`` picks the interval width, and every response that gets
past the gate carries ``x-ratelimit-limit: 4`` with a counting-down
``x-ratelimit-remaining`` and a millisecond ``x-ratelimit-reset``. Failure and
slowness can still be injected with query parameters on any API endpoint - see
``README.md`` next to this file.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as time_of_day
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from aiohttp import web
from aiohttp.typedefs import Handler

try:  # `python -m scripts.dev.mock` and the tests
    from scripts.dev.mock import keycloak
except ImportError:  # `python scripts/dev/mock/app.py`
    import keycloak  # type: ignore[no-redef]

_LOGGER: Final[logging.Logger] = logging.getLogger("mock-ckw")

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
FIXTURE_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"
CUSTOMER_FIXTURE: Final[Path] = FIXTURE_DIR / "customer.json"
MEASUREMENTS_DAYS_FIXTURE: Final[Path] = FIXTURE_DIR / "measurements_days.json"
MEASUREMENTS_MINUTES_FIXTURE: Final[Path] = FIXTURE_DIR / "measurements_minutes.json"

API_PREFIX: Final[str] = "/api/v1"
CUSTOMER_PATH: Final[str] = f"{API_PREFIX}/smartmeter/customer"
MEASUREMENT_PATH: Final[str] = (
    f"{API_PREFIX}/smartmeter/measurement/{{von}}/{{bis}}/{{aggregation}}"
)
DYNAMIC_PRICE_PATH: Final[str] = f"{API_PREFIX}/energie/dynamische-preise"

# Measured on the public CKW host: four requests per roughly one-second window.
RATE_LIMIT_LIMIT: Final[int] = 4
RATE_LIMIT_WINDOW_SECONDS: Final[float] = 1.0

# The deployed model's maximum request width is unknown; the error message is not.
# 400 days keeps a year-plus backfill working and still exercises the cap.
MAX_RANGE_DAYS: Final[int] = 400

# Error bodies, verbatim from the deployed API model (verification page, item 4).
ERROR_JWT_REQUIRED: Final[str] = "JWT Token is required."
ERROR_BAD_TOKEN: Final[str] = (
    "Bad or expired token. To fix, you should re-authenticate the user."
)
ERROR_CLIENT_ID: Final[str] = "The client id validation failed."
ERROR_TIMEOUT: Final[str] = "Timeout to get customer data was exceeded."
ERROR_QUOTA: Final[str] = "Quota has been exceeded"
ERROR_TOO_BIG: Final[str] = (
    "response too big, please adjust aggregation or duration accordingly"
)
ERROR_JWKS: Final[str] = "Error communicating with JWKS server."
ERROR_GENERIC: Final[str] = "An internal error has occurred."

# Tokens the mock treats as bad/expired. The real API validates a signature; the mock
# cannot, so these literals (and a JWT-shaped token whose ``exp`` has passed) stand in.
REJECTED_TOKENS: Final[frozenset[str]] = frozenset(
    {"invalid", "expired", "bad", "revoked"}
)

INJECTABLE_FAILURES: Final[frozenset[str]] = frozenset(
    {"401", "403", "408", "429", "quota", "500", "503"}
)
INJECTED_FAILURES: Final[dict[str, tuple[int, str]]] = {
    "401": (401, ERROR_BAD_TOKEN),
    "403": (403, ERROR_CLIENT_ID),
    "408": (408, ERROR_TIMEOUT),
    "429": (429, ERROR_QUOTA),
    "quota": (429, ERROR_QUOTA),
    "500": (500, ERROR_GENERIC),
    "503": (503, ERROR_JWKS),
}

# Exactly the ten values of the documented enum: no ``day(s)`` spelling exists in the
# CKW contract (see docs/reference/ckw-openenergy-api.md), so the mock rejects it too.
AGGREGATIONS: Final[frozenset[str]] = frozenset(
    {
        "year",
        "years",
        "month",
        "months",
        "week",
        "weeks",
        "day",
        "days",
        "minute",
        "minutes",
    }
)
INTERVAL_AGGREGATIONS: Final[frozenset[str]] = frozenset({"minute", "minutes"})
BUCKET_AGGREGATIONS: Final[frozenset[str]] = frozenset(
    {"year", "years", "month", "months", "week", "weeks"}
)

DATE_FORMAT: Final[str] = "%Y%m%d"
STAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"
ZURICH: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")

DYNAMIC_PRICE_DAY: Final[date] = date(2026, 11, 1)
DYNAMIC_PRICE_SEED: Final[int] = 20261101
DYNAMIC_PRICE_BASE: Final[float] = 0.24
DYNAMIC_PRICE_MIN: Final[float] = 0.11
DYNAMIC_PRICE_MAX: Final[float] = 0.42

QUANTITY_FIELDS: Final[tuple[str, ...]] = (
    "betrag_ht",
    "betrag_nt",
    "menge_fakturiert_ht",
    "menge_fakturiert_nt",
)
COUNTER_FIELDS: Final[tuple[str, ...]] = (
    "anzahl_linien_p",
    "anzahl_linien_fw",
    "anzahl_linien_fb",
)


@dataclass
class RateLimitWindow:
    """One in-memory rate-limit window: when it opened and how much it used."""

    started: float
    used: int


RATE_LIMIT: Final[web.AppKey[RateLimitWindow]] = web.AppKey(
    "rate_limit", RateLimitWindow
)


def _error_response(status: int, message: str) -> web.Response:
    """Build the single-key JSON error body the meter API really returns."""
    return web.json_response({"error": message}, status=status)


def _optional_int(raw: str | None, default: int) -> int:
    """Parse an optional integer query parameter, falling back to the default."""
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _optional_float(raw: str | None) -> float | None:
    """Parse an optional non-negative float query parameter."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _bearer_token(request: web.Request) -> str | None:
    """Return the bearer token, or ``None`` if the header is missing/malformed."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _jwt_expired(token: str) -> bool:
    """Read ``exp`` from a JWT-shaped token; ``False`` if it cannot be decoded."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return False
    expires = claims.get("exp") if isinstance(claims, dict) else None
    return isinstance(expires, int | float) and expires < time.time()


def _token_is_rejected(token: str) -> bool:
    """Decide whether the mock treats ``token`` as invalid or expired."""
    return token.lower() in REJECTED_TOKENS or _jwt_expired(token)


def _load_json(path: Path) -> Any:
    """Read a fixture on every request so edits show up without a restart."""
    return json.loads(path.read_text(encoding="utf-8"))


def _stamp(moment: datetime) -> str:
    """Format a UTC moment the way the CKW API does (no timezone suffix)."""
    return moment.astimezone(UTC).strftime(STAMP_FORMAT)


def _entry_day(entry: dict[str, Any]) -> date:
    """Return the Europe/Zurich calendar day a measurement entry belongs to."""
    moment = datetime.strptime(entry["zeitstempel_von_utc"], STAMP_FORMAT)
    return moment.replace(tzinfo=UTC).astimezone(ZURICH).date()


def _bucket_key(day: date, aggregation: str) -> tuple[str, int, int]:
    """Return the bucket a local day belongs to for week/month/year aggregation."""
    if aggregation.startswith("week"):
        iso = day.isocalendar()
        return ("week", iso.year, iso.week)
    if aggregation.startswith("month"):
        return ("month", day.year, day.month)
    return ("year", day.year, 0)


def _aggregate(entries: list[dict[str, Any]], aggregation: str) -> list[dict[str, Any]]:
    """Sum quantities/costs and max the counters per week, month or year."""
    buckets: dict[tuple[str, int, int], dict[str, Any]] = {}
    order: list[tuple[str, int, int]] = []
    for entry in entries:
        key = _bucket_key(_entry_day(entry), aggregation)
        if key not in buckets:
            buckets[key] = {
                "zeitstempel_von_utc": entry["zeitstempel_von_utc"],
                "zeitstempel_bis_utc": entry["zeitstempel_bis_utc"],
                **dict.fromkeys(QUANTITY_FIELDS, 0.0),
                **dict.fromkeys(COUNTER_FIELDS, 0),
            }
            order.append(key)
        bucket = buckets[key]
        bucket["zeitstempel_bis_utc"] = entry["zeitstempel_bis_utc"]
        for field in QUANTITY_FIELDS:
            bucket[field] += entry[field]
        for field in COUNTER_FIELDS:
            bucket[field] = max(bucket[field], entry[field])

    aggregated: list[dict[str, Any]] = []
    for key in order:
        bucket = buckets[key]
        aggregated.append(
            {
                **bucket,
                "betrag_ht": round(bucket["betrag_ht"], 2),
                "betrag_nt": round(bucket["betrag_nt"], 2),
                "menge_fakturiert_ht": round(bucket["menge_fakturiert_ht"], 1),
                "menge_fakturiert_nt": round(bucket["menge_fakturiert_nt"], 1),
            }
        )
    return aggregated


def _dynamic_prices() -> list[dict[str, Any]]:
    """Build 24 deterministic hourly day-ahead prices for one local day."""
    rng = random.Random(DYNAMIC_PRICE_SEED)
    start = datetime.combine(
        DYNAMIC_PRICE_DAY, time_of_day(0, 0), tzinfo=ZURICH
    ).astimezone(UTC)
    prices: list[dict[str, Any]] = []
    price = DYNAMIC_PRICE_BASE
    for hour in range(24):
        price = min(
            DYNAMIC_PRICE_MAX, max(DYNAMIC_PRICE_MIN, price + rng.uniform(-0.03, 0.03))
        )
        begin = start + timedelta(hours=hour)
        prices.append(
            {
                "zeitstempel_von_utc": _stamp(begin),
                "zeitstempel_bis_utc": _stamp(begin + timedelta(hours=1)),
                "preis_chf_kwh": round(price, 4),
            }
        )
    return prices


async def handle_customer(request: web.Request) -> web.StreamResponse:
    """``GET /api/v1/smartmeter/customer`` - the customer fixture."""
    return web.json_response(_load_json(CUSTOMER_FIXTURE))


async def handle_measurement(request: web.Request) -> web.StreamResponse:
    """``GET /api/v1/smartmeter/measurement/{von}/{bis}/{aggregation}``."""
    aggregation = request.match_info["aggregation"]
    if aggregation not in AGGREGATIONS:
        return _error_response(
            400,
            f"unsupported aggregation {aggregation!r}, "
            f"expected one of {', '.join(sorted(AGGREGATIONS))}",
        )

    try:
        von = datetime.strptime(request.match_info["von"], DATE_FORMAT).date()
        bis = datetime.strptime(request.match_info["bis"], DATE_FORMAT).date()
    except ValueError:
        return _error_response(400, "von and bis must be dates formatted as YYYYMMDD")
    if von > bis:
        return _error_response(400, "von must not be after bis")
    if request.query.get("range") == "too-big" or (bis - von).days + 1 > MAX_RANGE_DAYS:
        return _error_response(400, ERROR_TOO_BIG)

    fixture = (
        MEASUREMENTS_MINUTES_FIXTURE
        if aggregation in INTERVAL_AGGREGATIONS
        else MEASUREMENTS_DAYS_FIXTURE
    )
    document: dict[str, Any] = _load_json(fixture)
    series: dict[str, list[dict[str, Any]]] = document["series"]
    result = []
    for deliverypointnumber, entries in series.items():
        selected = [entry for entry in entries if von <= _entry_day(entry) <= bis]
        if aggregation in BUCKET_AGGREGATIONS:
            selected = _aggregate(selected, aggregation)
        result.append(
            {"deliverypointnumber": deliverypointnumber, "measurements": selected}
        )
    return web.json_response(result)


async def handle_dynamic_prices(request: web.Request) -> web.StreamResponse:
    """``GET /api/v1/energie/dynamische-preise`` - 24 hourly CHF/kWh prices."""
    return web.json_response(
        {"day": DYNAMIC_PRICE_DAY.strftime(DATE_FORMAT), "prices": _dynamic_prices()}
    )


async def handle_not_found(request: web.Request) -> web.StreamResponse:
    """Answer unknown paths with JSON instead of aiohttp's plain text."""
    return _error_response(404, f"no such endpoint: {request.method} {request.path}")


async def _dispatch(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Run the slowness knob, failure injection, and then the handler."""
    delay = _optional_float(request.query.get("slow"))
    if delay:
        await asyncio.sleep(delay)

    raw_fail = request.query.get("fail")
    if raw_fail is not None:
        if raw_fail not in INJECTABLE_FAILURES:
            return _error_response(
                400,
                f"fail must be one of {', '.join(sorted(INJECTABLE_FAILURES))}",
            )
        status, message = INJECTED_FAILURES[raw_fail]
        return _error_response(status, message)

    try:
        return await handler(request)
    except web.HTTPRedirection:
        raise
    except web.HTTPException as exc:
        message = exc.reason or HTTPStatus(exc.status).phrase
        return _error_response(exc.status, message)


@web.middleware
async def mock_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Apply the JWT gate, rate-limit headers and the error/slowness injection."""
    is_api = request.path.startswith(API_PREFIX)

    if is_api:
        token = _bearer_token(request)
        if token is None:
            # The real gate runs before routing and carries no x-ratelimit-* headers.
            return _error_response(400, ERROR_JWT_REQUIRED)
        if _token_is_rejected(token):
            response = _error_response(401, ERROR_BAD_TOKEN)
        else:
            response = await _dispatch(request, handler)
    else:
        response = await _dispatch(request, handler)

    window = request.app[RATE_LIMIT]
    now = time.monotonic()
    if now - window.started >= RATE_LIMIT_WINDOW_SECONDS:
        window.started = now
        window.used = 0
    window.used += 1

    remaining = max(RATE_LIMIT_LIMIT - window.used, 0)
    reset = max(
        int((window.started + RATE_LIMIT_WINDOW_SECONDS - now) * 1000),
        0,
    )
    if "ratelimit-remaining" in request.query:
        remaining = _optional_int(request.query.get("ratelimit-remaining"), remaining)
    if "ratelimit-reset" in request.query:
        reset = _optional_int(request.query.get("ratelimit-reset"), reset)

    response.headers["x-ratelimit-limit"] = str(RATE_LIMIT_LIMIT)
    response.headers["x-ratelimit-remaining"] = str(max(remaining, 0))
    response.headers["x-ratelimit-reset"] = str(max(reset, 0))
    return response


def register_api_routes(app: web.Application) -> None:
    """Register the three OpenEnergy endpoints on ``app``."""
    app.router.add_get(CUSTOMER_PATH, handle_customer)
    app.router.add_get(MEASUREMENT_PATH, handle_measurement)
    app.router.add_get(DYNAMIC_PRICE_PATH, handle_dynamic_prices)


def build_app() -> web.Application:
    """Build the complete mock: OpenEnergy API plus fake Keycloak."""
    app = web.Application(middlewares=[mock_middleware])
    app[RATE_LIMIT] = RateLimitWindow(started=time.monotonic(), used=0)
    register_api_routes(app)
    keycloak.register_keycloak_routes(app)
    app.router.add_route("*", "/{tail:.*}", handle_not_found)
    return app


def main(argv: list[str] | None = None) -> None:
    """Run the mock from the command line."""
    parser = argparse.ArgumentParser(description="Local mock of the CKW OpenEnergy API")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="bind address (default: 0.0.0.0, so a Podman container reaches it)",
    )
    parser.add_argument(
        "--port", type=int, default=8787, help="bind port (default: 8787)"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    _LOGGER.info(
        "mock CKW API on http://%s:%d%s, fake Keycloak on %s",
        args.host,
        args.port,
        API_PREFIX,
        keycloak.REALM_PREFIX,
    )
    _LOGGER.warning("this is a mock: fixtures only, tokens are not verifiable")
    web.run_app(build_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
