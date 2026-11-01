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

Every response carries ``x-ratelimit-limit``, ``x-ratelimit-remaining`` and
``x-ratelimit-reset``. Failure and slowness can be injected with query
parameters on any endpoint - see ``README.md`` next to this file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from datetime import UTC, date, datetime, time, timedelta
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
MEASUREMENTS_FIXTURE: Final[Path] = FIXTURE_DIR / "measurements_days.json"

API_PREFIX: Final[str] = "/api/v1"
CUSTOMER_PATH: Final[str] = f"{API_PREFIX}/smartmeter/customer"
MEASUREMENT_PATH: Final[str] = (
    f"{API_PREFIX}/smartmeter/measurement/{{von}}/{{bis}}/{{aggregation}}"
)
DYNAMIC_PRICE_PATH: Final[str] = f"{API_PREFIX}/energie/dynamische-preise"

RATE_LIMIT_LIMIT: Final[int] = 100
RATE_LIMIT_REMAINING: Final[int] = 97
RATE_LIMIT_RESET: Final[int] = 42_000
INJECTABLE_STATUSES: Final[frozenset[int]] = frozenset({401, 403, 408, 429, 500, 503})
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

DATE_FORMAT: Final[str] = "%Y%m%d"
STAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"
ZURICH: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")

DYNAMIC_PRICE_DAY: Final[date] = date(2026, 11, 1)
DYNAMIC_PRICE_SEED: Final[int] = 20261101
DYNAMIC_PRICE_BASE: Final[float] = 0.24
DYNAMIC_PRICE_MIN: Final[float] = 0.11
DYNAMIC_PRICE_MAX: Final[float] = 0.42

ERROR_CODES: Final[dict[int, str]] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    408: "request_timeout",
    429: "too_many_requests",
    500: "internal_server_error",
    503: "service_unavailable",
}


def _error_code(status: int) -> str:
    """Return the snake_case error slug for an HTTP status."""
    if status in ERROR_CODES:
        return ERROR_CODES[status]
    try:
        return HTTPStatus(status).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "error"


def _error_response(status: int, detail: str) -> web.Response:
    """Build the JSON error body every endpoint uses."""
    return web.json_response(
        {"error": _error_code(status), "status": status, "detail": detail},
        status=status,
    )


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


def _has_bearer_token(request: web.Request) -> bool:
    """Check for a syntactically plausible ``Authorization: Bearer`` header."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return scheme.lower() == "bearer" and bool(token.strip())


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


def _dynamic_prices() -> list[dict[str, Any]]:
    """Build 24 deterministic hourly day-ahead prices for one local day."""
    rng = random.Random(DYNAMIC_PRICE_SEED)
    start = datetime.combine(DYNAMIC_PRICE_DAY, time(0, 0), tzinfo=ZURICH).astimezone(
        UTC
    )
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
    if not _has_bearer_token(request):
        return _error_response(401, "missing or malformed bearer token")
    return web.json_response(_load_json(CUSTOMER_FIXTURE))


async def handle_measurement(request: web.Request) -> web.StreamResponse:
    """``GET /api/v1/smartmeter/measurement/{von}/{bis}/{aggregation}``."""
    if not _has_bearer_token(request):
        return _error_response(401, "missing or malformed bearer token")

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

    document: dict[str, Any] = _load_json(MEASUREMENTS_FIXTURE)
    series: dict[str, list[dict[str, Any]]] = document["series"]
    return web.json_response(
        [
            {
                "deliverypointnumber": deliverypointnumber,
                "measurements": [
                    entry for entry in entries if von <= _entry_day(entry) <= bis
                ],
            }
            for deliverypointnumber, entries in series.items()
        ]
    )


async def handle_dynamic_prices(request: web.Request) -> web.StreamResponse:
    """``GET /api/v1/energie/dynamische-preise`` - 24 hourly CHF/kWh prices."""
    if not _has_bearer_token(request):
        return _error_response(401, "missing or malformed bearer token")
    return web.json_response(
        {"day": DYNAMIC_PRICE_DAY.strftime(DATE_FORMAT), "prices": _dynamic_prices()}
    )


async def handle_not_found(request: web.Request) -> web.StreamResponse:
    """Answer unknown paths with JSON instead of aiohttp's plain text."""
    return _error_response(404, f"no such endpoint: {request.method} {request.path}")


@web.middleware
async def mock_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Add rate-limit headers and implement the error/slowness injection."""
    remaining = max(
        _optional_int(request.query.get("ratelimit-remaining"), RATE_LIMIT_REMAINING), 0
    )
    reset = max(
        _optional_int(request.query.get("ratelimit-reset"), RATE_LIMIT_RESET), 0
    )

    delay = _optional_float(request.query.get("slow"))
    if delay:
        await asyncio.sleep(delay)

    raw_fail = request.query.get("fail")
    fail = _optional_int(raw_fail, -1)
    if raw_fail is not None and fail not in INJECTABLE_STATUSES:
        response: web.StreamResponse = _error_response(
            400,
            f"fail must be one of {', '.join(str(s) for s in sorted(INJECTABLE_STATUSES))}",
        )
    elif raw_fail is not None:
        response = _error_response(fail, f"injected failure {fail} for {request.path}")
    else:
        try:
            response = await handler(request)
        except web.HTTPRedirection:
            raise
        except web.HTTPException as exc:
            response = _error_response(
                exc.status, exc.reason or _error_code(exc.status)
            )

    response.headers["x-ratelimit-limit"] = str(RATE_LIMIT_LIMIT)
    response.headers["x-ratelimit-remaining"] = str(remaining)
    response.headers["x-ratelimit-reset"] = str(reset)
    return response


def register_api_routes(app: web.Application) -> None:
    """Register the three OpenEnergy endpoints on ``app``."""
    app.router.add_get(CUSTOMER_PATH, handle_customer)
    app.router.add_get(MEASUREMENT_PATH, handle_measurement)
    app.router.add_get(DYNAMIC_PRICE_PATH, handle_dynamic_prices)


def build_app() -> web.Application:
    """Build the complete mock: OpenEnergy API plus fake Keycloak."""
    app = web.Application(middlewares=[mock_middleware])
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
