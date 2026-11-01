"""Constants for the CKW Smart Meter integration.

Every constant, URL and tunable lives in this module
(``docs/reference/conventions.md``): later modules read them and never spell out a
magic string or number themselves. Nothing here performs I/O.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from homeassistant.const import Platform, UnitOfEnergy

DOMAIN: Final = "ckw"
NAME: Final = "CKW Smart Meter"
VERSION: Final = "0.1.0"

# The service device every per-meter device hangs under (ADR-0003).
MANUFACTURER: Final = "CKW"
SERVICE_MODEL: Final = "OpenEnergy API"

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

# ---------------------------------------------------------------------------
# Long-term statistics (ADR-0002, ADR-0009, #84-#89)
# ---------------------------------------------------------------------------

#: Days one backfill request covers. A window this wide is far below the server's
#: "response too big" cap, so a 730-day job never trips it (#147).
BACKFILL_CHUNK_DAYS: Final[int] = 30
#: Seconds paused between two backfill chunks. Conservative on purpose: the
#: observed quota is about four requests per second (#147).
BACKFILL_PAUSE_SECONDS: Final[float] = 1.0
#: Upper bound the ``ckw.backfill`` service accepts, so a typo cannot ask for a decade.
MAX_BACKFILL_DAYS: Final[int] = BACKFILL_DAYS * 5

#: Sensor key whose entity id becomes the statistic id (ADR-0002, #84).
STATISTIC_ENTITY_KEY: Final = "consumption_total"
#: The recorder only accepts hourly rows whose unit is a valid energy unit
#: (docs/explanation/quarter-hour-history.md).
STATISTIC_UNIT: Final = UnitOfEnergy.KILO_WATT_HOUR
#: Required from Home Assistant 2026.11 on; omitting it logs a deprecation.
STATISTIC_UNIT_CLASS: Final = "energy"
#: ``source`` of an entity statistic: routed to the entity, so the Energy
#: dashboard's picker lists it (ADR-0002, ADR-0009).
STATISTIC_SOURCE: Final = "recorder"

#: Storage key prefix and schema version of the per-entry statistics state.
STORAGE_KEY_STATISTICS: Final = f"{DOMAIN}.statistics"
STORAGE_VERSION_STATISTICS: Final[int] = 1

# ---------------------------------------------------------------------------
# The `ckw.backfill` service (#123)
# ---------------------------------------------------------------------------

SERVICE_BACKFILL: Final = "backfill"
ATTR_DAYS: Final = "days"
ATTR_FORCE: Final = "force"
ATTR_ENTRY_ID: Final = "entry_id"

# ---------------------------------------------------------------------------
# The coordinator's rolling window and its log thresholds (#68, #69, #149)
# ---------------------------------------------------------------------------

#: Days requested on every poll, ending yesterday (ADR-0004).
ROLLING_WINDOW_DAYS: Final[int] = 7
#: A window always ends yesterday: today's aggregate does not exist yet.
WINDOW_END_DAYS_BACK: Final[int] = 1
#: The window after a "response too big" 400 is halved once, never below this.
MIN_WINDOW_DAYS: Final[int] = 1
#: Daily aggregates for the rolling window and every sensor total (PLAN #65).
DAYS_AGGREGATION: Final = "days"
#: The finest aggregation CKW serves: 15-minute rows for the quarter-hour sensor (#152).
MINUTES_AGGREGATION: Final = "minutes"
#: Width of one quarter-hour interval, used to reject wider rows from the minute endpoint.
QUARTER_HOUR_MINUTES: Final[int] = 15

#: Sleep before a request once fewer than this many requests are left.
RATE_LIMIT_MIN_REMAINING: Final[int] = 1

SECONDS_PER_HOUR: Final[int] = 3600
#: Warn above this lag: the observed spread is 1-3 days (#149, ADR-0004).
PUBLICATION_LAG_WARNING_HOURS: Final[int] = 96
#: Warn when the newest interval has not moved for this many days (#149).
PUBLICATION_STALL_DAYS: Final[int] = 4

REFRESH_SKEW_SECONDS: Final[int] = 60

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
#: Whether setup imports the 730-day history in the background. Absent means
#: enabled, so an existing entry keeps backfilling after the upgrade. A user who
#: does not want the initial history import can turn it off in the options flow.
CONF_BACKFILL_ENABLED: Final = "backfill_enabled"

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
