"""The redacted config-entry dump behind Home Assistant's diagnostics download.

Gold rule ``diagnostics`` (#105): the file a user attaches to a bug report must
explain a poll without handing out anything that identifies them. What the dump
answers: which version, which options, which meters, whether the last poll
worked and why it did not, how far the statistics import got, and whether the
session still has its tokens.

What it never contains (``docs/reference/conventions.md``, "Redaction"):

* an access token, a refresh token, a password or an ``Authorization`` header -
  the session shows two booleans and the expiry time, nothing else;
* a raw ``deliverypointnumber`` - only its last four characters, through
  ``api.mask_deliverypoint``, exactly as the debug log does;
* the customer id - the entry's unique id *is* the customer number (ADR-0008),
  so it appears as ``sha256``'s first six hex characters and nothing more, and
  the entry title carries the same number, so the title is redacted the same way;
* the e-mail address, the phone number, the street or the location of the
  account - none of them is read here, and none is ever put in the dump.

Values are masked, keys are kept: a dump whose key vanished tells a reader
nothing, and a missing key would hide a regression. The last word belongs to
``_redact_secrets``: it walks the finished dict and drops the *value* of every
key that names a credential, keeping booleans. A field added later by someone
who did not read this page therefore still cannot leak a secret.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final, cast

from .api import RateLimitInfo, mask_deliverypoint
from .const import (
    CONF_BACKFILL_ENABLED,
    CONF_CUSTOMER_ID,
    CONF_POLL_HOURS,
    DEFAULT_POLL_HOURS,
    VERSION,
)
from .coordinator import CkwCoordinator, CkwData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import CkwConfigEntry
    from .statistics import StatisticsState

__all__ = ["async_get_config_entry_diagnostics"]

type Diagnostics = dict[str, Any]

#: Hex characters of the customer-id hash in the dump. Six is enough to tell two
#: accounts in a bug report apart and too short to recover the number.
CUSTOMER_HASH_CHARS: Final[int] = 6

#: Key names whose value is a credential. The final pass drops every match that
#: is not a boolean, so ``has_access_token: true`` survives and
#: ``access_token: "ey..."`` cannot.
SECRET_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"token|secret|password|authorization|refresh|access",
    re.IGNORECASE,
)

#: Name of the OAuth2 token dict ``config_entry_oauth2_flow`` keeps in the entry.
TOKEN_KEY: Final = "token"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CkwConfigEntry
) -> Diagnostics:
    """Return the redacted dump Home Assistant downloads for one entry.

    ``hass`` is unused: everything the dump reports already lives on the entry,
    in its runtime data and on the coordinator, and nothing here may send a
    request. A part that is missing - no runtime data yet, no successful poll,
    no statistics state - is reported as absent rather than skipped, so the
    dump's shape does not depend on when it was taken.
    """
    runtime = getattr(entry, "runtime_data", None)
    coordinator: CkwCoordinator | None = None if runtime is None else runtime.coordinator
    data: CkwData | None = None if coordinator is None else coordinator.data
    customer_id = _customer_id(entry)
    customer_hash = _customer_hash(customer_id)

    report: Diagnostics = {
        "version": VERSION,
        "entry": {
            "title": _redact_title(entry.title, customer_id, customer_hash),
            "unique_id": customer_hash,
            "options": {
                CONF_POLL_HOURS: entry.options.get(CONF_POLL_HOURS, DEFAULT_POLL_HOURS),
                CONF_BACKFILL_ENABLED: bool(entry.options.get(CONF_BACKFILL_ENABLED, True)),
            },
        },
        "customer": {"id_hash": customer_hash},
        "meters": _meters_report(data),
        "coordinator": _coordinator_report(coordinator, data),
        "statistics": _statistics_report(None if runtime is None else runtime.statistics_state),
        "session": _session_report(entry),
    }
    return cast("Diagnostics", _redact_secrets(report))


# ---------------------------------------------------------------------------
# The parts of the dump
# ---------------------------------------------------------------------------


def _meters_report(data: CkwData | None) -> list[Diagnostics]:
    """Return one row per known delivery point, each carrying its masked id.

    Both the meters the customer payload lists and the ones a poll has data for
    are reported: a meter that vanished from the account but is still in the
    totals is exactly what a stale-device report is about.
    """
    if data is None:
        return []
    reports: list[Diagnostics] = []
    for deliverypoint in sorted(set(data.meters) | set(data.meter_info)):
        totals = data.meters.get(deliverypoint)
        meter = data.meter_info.get(deliverypoint)
        reports.append(
            {
                "deliverypoint": mask_deliverypoint(deliverypoint),
                "model": None if meter is None else _text(meter.model),
                "type": None if meter is None else _text(meter.type),
                "fuel": None if meter is None else _text(meter.fuel),
                "last_interval_end": None if totals is None else _iso(totals.last_interval_end),
                "publication_delay_h": (
                    None
                    if totals is None or totals.publication_delay_h is None
                    else round(totals.publication_delay_h, 1)
                ),
                "intervals_seen": 0 if totals is None else totals.intervals_seen,
                "quality_intervals": 0 if totals is None else totals.quality_intervals,
                "present": deliverypoint in data.meters_present,
                "stale_since": _iso(data.stale_since.get(deliverypoint)),
            }
        )
    return reports


def _coordinator_report(coordinator: CkwCoordinator | None, data: CkwData | None) -> Diagnostics:
    """Return the health of the last poll: success, failure type, rate limit, interval.

    Only the *type* of the last exception is reported. Its message is not: it is
    built from a translated key and may quote a URL or a response body, and the
    type is what a bug report needs ("is it a timeout or a 401?").
    """
    report: Diagnostics = {
        "last_update_success": coordinator is not None and coordinator.last_update_success,
        "last_exception": None,
        "last_rate_limit": None,
        "poll_hours": DEFAULT_POLL_HOURS,
    }
    if coordinator is None:
        return report

    report["poll_hours"] = coordinator.poll_hours
    exception = coordinator.last_exception
    if exception is not None:
        report["last_exception"] = type(exception).__name__
    rate_limit = data.rate_limit if data is not None else coordinator.client.last_rate_limit
    report["last_rate_limit"] = _rate_limit_report(rate_limit)
    return report


def _rate_limit_report(info: RateLimitInfo | None) -> Diagnostics | None:
    """Return the three ``x-ratelimit-*`` values of the newest response."""
    if info is None:
        return None
    return {
        "limit": info.limit,
        "remaining": info.remaining,
        "reset_ms": info.reset_ms,
    }


def _statistics_report(state: StatisticsState | None) -> Diagnostics:
    """Return the backfill's progress, so a stuck import is visible in the dump."""
    if state is None:
        return {
            "done": False,
            "running": False,
            "days": None,
            "next_start": None,
            "chunks": {"done": 0, "total": 0},
        }
    return {
        "done": state.done,
        "running": state.running,
        "days": state.days,
        "next_start": _date_iso(state.next_start),
        "chunks": {"done": state.chunks_done, "total": state.chunks_total},
    }


def _session_report(entry: CkwConfigEntry) -> Diagnostics:
    """Return whether the session holds its tokens, and when it expires.

    The tokens themselves never leave the entry: only the two booleans and the
    expiry timestamp are reported. ``expires_at`` is the access token's expiry,
    the only one the OAuth2 session stores; a missing token dict reads as "no
    session", not as an error.
    """
    token = entry.data.get(TOKEN_KEY)
    mapping: Mapping[str, Any] = token if isinstance(token, Mapping) else {}
    expires_at = mapping.get("expires_at")
    moment = (
        None
        if not isinstance(expires_at, int | float)
        else datetime.fromtimestamp(expires_at, UTC).isoformat()
    )
    return {
        "has_access_token": bool(mapping.get("access_token")),
        "has_refresh_token": bool(mapping.get("refresh_token")),
        "expires_at": moment,
        "expired": None if expires_at is None else expires_at <= time.time(),
    }


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


def _customer_id(entry: CkwConfigEntry) -> str:
    """Return the customer number the entry belongs to, or an empty string.

    The config flow writes it to the entry data and uses it as the unique id, so
    the dump still has something to hash for an entry written before it existed.
    """
    configured = entry.data.get(CONF_CUSTOMER_ID)
    if isinstance(configured, str) and configured:
        return configured
    return entry.unique_id or ""


def _customer_hash(customer_id: str) -> str:
    """Return the short hash the dump shows instead of the customer number."""
    digest = hashlib.sha256(customer_id.encode("utf-8")).hexdigest()
    return digest[:CUSTOMER_HASH_CHARS]


def _redact_title(title: str, customer_id: str, customer_hash: str) -> str:
    """Return the entry title with the customer number replaced by its hash.

    The title is the one place the number is unavoidable - it is what the user
    named the entry - so it stays readable while the number does not (ADR-0008,
    "The title carries the customer id, which is personal data").
    """
    if not customer_id:
        return title
    return title.replace(customer_id, customer_hash)


def _redact_secrets(value: Any) -> Any:
    """Drop the value of every key that names a credential, keeping booleans.

    This is the net under the whole dump: the shape above is written to be safe,
    and this pass makes a field added later safe too. A boolean under such a key
    is a fact about the credential ("it is there"), never the credential.
    """
    if isinstance(value, Mapping):
        return {
            key: _redact_secrets(item)
            for key, item in value.items()
            if isinstance(item, bool) or not SECRET_KEY_PATTERN.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    return value


def _text(value: object) -> str | None:
    """Return an enum or string as text, so the dump holds no unknown objects."""
    return None if value is None else str(value)


def _iso(moment: datetime | None) -> str | None:
    """Return a UTC timestamp as ISO text, or ``None`` when there is none."""
    return None if moment is None else moment.isoformat()


def _date_iso(day: date | None) -> str | None:
    """Return a local calendar day as ISO text, or ``None`` when there is none."""
    return None if day is None else day.isoformat()
