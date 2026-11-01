"""Constants for the CKW Smart Meter integration.

Every constant, URL and tunable lives in this module
(``docs/reference/conventions.md``): later modules read them and never spell out a
magic string or number themselves. Nothing here performs I/O.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from homeassistant.const import Platform

DOMAIN: Final = "ckw"
NAME: Final = "CKW Smart Meter"
VERSION: Final = "0.1.0"

PLATFORMS: list[Platform] = [Platform.SENSOR]

# All date arithmetic happens in Europe/Zurich, never by the UTC date
# (docs/reference/conventions.md, "Time").
TIME_ZONE: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")

# ---------------------------------------------------------------------------
# Production endpoints (docs/reference/ckw-openenergy-api.md)
# ---------------------------------------------------------------------------

API_BASE_URL: Final = "https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1"

CKW_REALM_URL: Final = "https://id.ckw.ch/realms/CKW-prod"
AUTHORIZE_URL: Final = f"{CKW_REALM_URL}/protocol/openid-connect/auth"
TOKEN_URL: Final = f"{CKW_REALM_URL}/protocol/openid-connect/token"

SCOPES: Final[list[str]] = ["openid", "offline_access"]

# Exactly the ten values the API accepts, singular and plural spelling included.
AGGREGATIONS: Final[tuple[str, ...]] = (
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
)

# ---------------------------------------------------------------------------
# Tunables (ADR-0004)
# ---------------------------------------------------------------------------

DEFAULT_POLL_HOURS: Final[int] = 2
MIN_POLL_HOURS: Final[int] = 1
MAX_POLL_HOURS: Final[int] = 12
# The options flow offers exactly these intervals (ADR-0004, #53).
POLL_INTERVAL_HOURS: Final[tuple[int, ...]] = (1, 2, 4, 6, 12)
BACKFILL_DAYS: Final[int] = 730
ROLLING_WINDOW_DAYS: Final[int] = 7
REFRESH_SKEW_SECONDS: Final[int] = 60
PUBLICATION_LAG_WARNING_HOURS: Final[int] = 48

# ---------------------------------------------------------------------------
# Config keys: the endpoint override (#39) that switches mock and production
# ---------------------------------------------------------------------------

CONF_BASE_URL: Final = "base_url"
CONF_AUTHORIZE_URL: Final = "authorize_url"
CONF_TOKEN_URL: Final = "token_url"

# Config-entry data and options written by the config flow.
CONF_CUSTOMER_ID: Final = "customer_id"
CONF_CITY: Final = "city"
CONF_METER: Final = "meter"
CONF_POLL_HOURS: Final = "poll_hours"

# Development overrides for the endpoints (scripts/dev/.env.example).
ENV_API_BASE_URL: Final = "CKW_API_BASE_URL"
ENV_AUTHORIZE_URL: Final = "CKW_AUTHORIZE_URL"
ENV_TOKEN_URL: Final = "CKW_TOKEN_URL"

# ---------------------------------------------------------------------------
# Local mock defaults (`mise run dev-mock`, scripts/dev/mock/README.md)
# ---------------------------------------------------------------------------

MOCK_HOST: Final = "http://localhost:8787"
MOCK_REALM_URL: Final = f"{MOCK_HOST}/realms/CKW-prod"
MOCK_BASE_URL: Final = f"{MOCK_HOST}/api/v1"
MOCK_AUTHORIZE_URL: Final = f"{MOCK_REALM_URL}/protocol/openid-connect/auth"
MOCK_TOKEN_URL: Final = f"{MOCK_REALM_URL}/protocol/openid-connect/token"

# Fixture mode (#39) is off unless this variable is set; the variable is only
# named here until the API layer learns to read it.
FIXTURE_MODE_ENV: Final = "CKW_FIXTURE_MODE"

# ---------------------------------------------------------------------------
# Meter enums of the measured API model (docs/reference/ckw-openenergy-api.md)
# ---------------------------------------------------------------------------


class MeterModel(StrEnum):
    """Value of a meter's ``model`` field."""

    SMART = "SMART"
    ANALOGUE = "ANALOGUE"


class MeterType(StrEnum):
    """Value of a meter's ``type`` field; ``export``/``production`` mean feed-in."""

    IMPORT = "import"
    EXPORT = "export"
    CONSUMPTION = "consumption"
    PRODUCTION = "production"


class Fuel(StrEnum):
    """Value of a meter's ``fuel`` field; ``elec`` filters out gas meters."""

    ELEC = "elec"
    GAS = "gas"
