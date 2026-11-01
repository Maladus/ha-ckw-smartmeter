"""Tests for the redacted diagnostics dump (Gold rule ``diagnostics``, #105).

The dump is built from a real config entry and the real coordinator, the sensor
platform included: only ``CkwClient.async_get_customer`` and
``CkwClient.async_get_measurements`` are patched, so the daily window comes from
``tests/fixtures/measurements_days.json`` and the meter metadata from
``tests/fixtures/customer.json``. Nothing here opens a socket.

The redaction is asserted on the *serialized* dump, because a promise about a
dict is not a promise about the file a user attaches to a bug report: no
token-shaped string, no complete Zählpunkt, no e-mail address, and the customer
number only as its hash. The last test proves the safety pass that makes a
future field safe, not just the fields this file happens to know.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ckw import statistics as statistics_module
from custom_components.ckw.api import (
    CkwClient,
    CkwCustomer,
    CkwMeasurement,
    CkwMeterMeasurements,
    RateLimitInfo,
    _parse_customer,
    mask_deliverypoint,
)
from custom_components.ckw.const import (
    CONF_BACKFILL_ENABLED,
    CONF_CITY,
    CONF_CUSTOMER_ID,
    CONF_METER,
    CONF_POLL_HOURS,
    DAYS_AGGREGATION,
    DOMAIN,
    MINUTES_AGGREGATION,
    TIME_ZONE,
    VERSION,
)
from custom_components.ckw.coordinator import CkwData, MeterTotals
from custom_components.ckw.diagnostics import (
    CUSTOMER_HASH_CHARS,
    _meters_report,
    _rate_limit_report,
    _redact_secrets,
    _redact_title,
    async_get_config_entry_diagnostics,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "hass_storage")

FIXTURE_DIR: Final[Path] = Path(__file__).parent / "fixtures"
CUSTOMER_ID: Final = "0000000099"
OTHER_CUSTOMER_ID: Final = "0000000042"
CITY: Final = "Luzern"
IMPORT_METER: Final = "CH1003601234500000000000000000000"
EXPORT_METER: Final = "CH1003601234500000000000000000001"

#: Frozen poll time, winter (CET, UTC+1): 2026-11-02 12:00 local, 11:00 UTC.
FROZEN_NOW: Final = datetime(2026, 11, 2, 12, 0, tzinfo=TIME_ZONE)

#: The headers one real CKW response carries, for the coordinator's health block.
RATE_LIMIT: Final = RateLimitInfo(limit=100, remaining=99, reset_ms=60_000)

#: What a token looks like in a dump, and what an e-mail address looks like.
TOKEN_PATTERN: Final = re.compile(r"ey[A-Za-z0-9_-]{20,}")
EMAIL_PATTERN: Final = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# Assembled from pieces on purpose: a literal three-part JWT is what the
# gitleaks ``jwt`` rule looks for, and this repository scans its own tests.
ACCESS_TOKEN: Final = (
    "ey" + "JhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.ey" + "JzdWIiOiIwMDAwMDAwMDk5In0.signature"
)
REFRESH_TOKEN: Final = (
    "ey" + "JyZWZyZXNoIjoiMDAwMDAwMDA5OSIsImEi" + "OiJiIn0.ey" + "J0eXAiOiJKV1QifQ.signature"
)


# ---------------------------------------------------------------------------
# Fixtures as the API returns them
# ---------------------------------------------------------------------------


def _utc(value: str) -> datetime:
    """Parse a fixture timestamp, which is a naive UTC ``dateTimeOnly``."""
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _measurement(entry: dict[str, Any]) -> CkwMeasurement:
    """Build the parsed measurement of one fixture entry."""
    return CkwMeasurement(
        start_utc=_utc(entry["zeitstempel_von_utc"]),
        end_utc=_utc(entry["zeitstempel_bis_utc"]),
        kwh_ht=entry["menge_fakturiert_ht"],
        kwh_nt=entry["menge_fakturiert_nt"],
        chf_ht=entry["betrag_ht"],
        chf_nt=entry["betrag_nt"],
        lines_p=entry["anzahl_linien_p"],
        lines_fw=entry["anzahl_linien_fw"],
        lines_fb=entry["anzahl_linien_fb"],
    )


def load_series(name: str) -> list[CkwMeterMeasurements]:
    """Replay one committed measurement fixture as the API would return it."""
    document = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return [
        CkwMeterMeasurements(
            deliverypointnumber=deliverypoint,
            measurements=tuple(_measurement(entry) for entry in entries),
        )
        for deliverypoint, entries in document["series"].items()
    ]


def load_customer() -> CkwCustomer:
    """Parse the committed customer fixture with the real API parser."""
    document = json.loads((FIXTURE_DIR / "customer.json").read_text(encoding="utf-8"))
    return _parse_customer(document)


def patch_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the two CKW calls the coordinator makes, without a socket."""
    customer = load_customer()
    days = load_series("measurements_days.json")
    minutes = load_series("measurements_minutes.json")

    async def get_customer(_client: CkwClient) -> CkwCustomer:
        return customer

    async def get_measurements(
        client: CkwClient,
        start: date,
        end: date,
        aggregation: str = DAYS_AGGREGATION,
    ) -> list[CkwMeterMeasurements]:
        # A real response leaves its rate-limit headers on the client.
        client.last_rate_limit = RATE_LIMIT
        return minutes if aggregation == MINUTES_AGGREGATION else days

    monkeypatch.setattr(CkwClient, "async_get_customer", get_customer)
    monkeypatch.setattr(CkwClient, "async_get_measurements", get_measurements)
    # The poll's statistics import writes to the recorder; it needs an instance
    # these tests do not start, and the dump has nothing to say about it.
    monkeypatch.setattr(statistics_module, "async_import_statistics", lambda *args: None)


# ---------------------------------------------------------------------------
# Config entry and OAuth2 helpers
# ---------------------------------------------------------------------------


class FakeOAuth2Implementation(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    """Local OAuth2 implementation that hands out token-shaped, fake tokens."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Store the Home Assistant instance."""
        self.hass = hass

    @property
    def name(self) -> str:
        """Return the implementation name."""
        return "CKW test IdP"

    @property
    def domain(self) -> str:
        """Return the auth implementation domain stored in the entry."""
        return DOMAIN

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Return a URL the test never opens."""
        return "https://id.example.invalid/authorize"

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Return the token pair the callback would resolve to."""
        return {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    async def _async_refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        """Return a refreshed token pair."""
        return {
            **token,
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "expires_in": 3600,
        }


def add_entry(
    hass: HomeAssistant,
    *,
    customer_id: str = CUSTOMER_ID,
    meter: str = IMPORT_METER,
    poll_hours: int = 2,
    token: bool = True,
    store_customer_id: bool = True,
) -> MockConfigEntry:
    """Add one configured CKW account, with its token pair by default."""
    data: dict[str, Any] = {
        CONF_CITY: CITY,
        CONF_METER: meter,
        "auth_implementation": DOMAIN,
    }
    if store_customer_id:
        data[CONF_CUSTOMER_ID] = customer_id
    if token:
        data["token"] = {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "expires_in": 3600,
            "expires_at": time.time() + 3600,
            "token_type": "Bearer",
        }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        # The backfill is off: these tests read the dump, and a 730-day import
        # in the background would only slow the setup down.
        options={CONF_POLL_HOURS: poll_hours, CONF_BACKFILL_ENABLED: False},
        unique_id=customer_id,
        title=f"{customer_id} — {CITY}",
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: Any,
) -> ConfigEntry:
    """Set up the real integration against the replayed fixtures."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, FakeOAuth2Implementation(hass)
    )
    patch_api(monkeypatch)
    entry = add_entry(hass, **kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def customer_hash(customer_id: str) -> str:
    """The short hash the dump must show instead of the customer number."""
    return hashlib.sha256(customer_id.encode("utf-8")).hexdigest()[:CUSTOMER_HASH_CHARS]


def serialized(dump: dict[str, Any]) -> str:
    """Return the dump exactly as Home Assistant writes the download."""
    return json.dumps(dump, indent=2)


# ---------------------------------------------------------------------------
# The dump of a polled entry
# ---------------------------------------------------------------------------


@pytest.fixture
async def dump(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Return the dump of one polled entry, taken at the frozen poll time."""
    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch)
        return await async_get_config_entry_diagnostics(hass, entry)


async def test_the_dump_has_the_expected_shape(dump: dict[str, Any]) -> None:
    """Every part a bug report needs is present, with the documented keys."""
    diagnostics = dump

    assert diagnostics["version"] == VERSION
    assert set(diagnostics) == {
        "version",
        "entry",
        "customer",
        "meters",
        "coordinator",
        "statistics",
        "session",
    }
    assert diagnostics["entry"]["options"] == {CONF_POLL_HOURS: 2, CONF_BACKFILL_ENABLED: False}

    meters = {meter["deliverypoint"]: meter for meter in diagnostics["meters"]}
    meter = meters[mask_deliverypoint(IMPORT_METER)]
    assert set(meter) == {
        "deliverypoint",
        "model",
        "type",
        "fuel",
        "last_interval_end",
        "publication_delay_h",
        "intervals_seen",
        "quality_intervals",
        "present",
        "stale_since",
    }
    assert meter["model"] == "SMART"
    assert meter["type"] == "import"
    assert meter["fuel"] == "elec"
    assert meter["present"] is True
    assert meter["intervals_seen"] > 0
    assert meter["stale_since"] is None
    # ISO text, not a datetime object: the download has to serialize.
    assert datetime.fromisoformat(meter["last_interval_end"]).tzinfo is not None
    assert isinstance(meter["publication_delay_h"], float)

    coordinator = diagnostics["coordinator"]
    assert coordinator["last_update_success"] is True
    assert coordinator["last_exception"] is None
    assert coordinator["poll_hours"] == 2
    assert coordinator["last_rate_limit"] == {"limit": 100, "remaining": 99, "reset_ms": 60_000}

    statistics = diagnostics["statistics"]
    assert set(statistics) == {"done", "running", "days", "next_start", "chunks"}
    assert set(statistics["chunks"]) == {"done", "total"}

    session = diagnostics["session"]
    assert session["has_access_token"] is True
    assert session["has_refresh_token"] is True
    assert session["expired"] is False
    assert datetime.fromisoformat(session["expires_at"]).tzinfo is not None

    serialized(diagnostics)


async def test_the_dump_leaks_no_token_no_full_delivery_point_and_no_email(
    dump: dict[str, Any],
) -> None:
    """The text of the download holds none of the three things a dump can leak."""
    text = serialized(dump)

    # The pattern must catch the tokens the entry really carries, or the
    # assertion below would pass for the wrong reason.
    assert TOKEN_PATTERN.search(ACCESS_TOKEN)
    assert TOKEN_PATTERN.search(REFRESH_TOKEN)
    assert ACCESS_TOKEN not in text
    assert REFRESH_TOKEN not in text
    assert TOKEN_PATTERN.search(text) is None, "a token-shaped string reached the dump"

    # Every delivery point of the fixture, complete: none may appear.
    for series in load_series("measurements_days.json"):
        deliverypoint = series.deliverypointnumber
        assert len(deliverypoint) == 33
        assert deliverypoint not in text, "a complete delivery point reached the dump"
        assert mask_deliverypoint(deliverypoint) in text

    # The customer payload carries an e-mail address; the dump must not.
    assert EMAIL_PATTERN.search("max@example.invalid")
    assert EMAIL_PATTERN.search(text) is None, "an e-mail address reached the dump"


async def test_the_customer_id_appears_only_as_its_hash(dump: dict[str, Any]) -> None:
    """The entry's unique id is the customer number, so the dump shows its hash."""
    text = serialized(dump)

    assert dump["entry"]["unique_id"] == customer_hash(CUSTOMER_ID)
    assert dump["customer"]["id_hash"] == customer_hash(CUSTOMER_ID)
    assert CUSTOMER_ID not in text
    assert customer_hash(CUSTOMER_ID) in text
    # The title is `<customer id> — <city>`; the number is replaced, not dropped,
    # so a reader can still tell two entries apart.
    assert dump["entry"]["title"] == f"{customer_hash(CUSTOMER_ID)} — {CITY}"


async def test_two_entries_produce_independent_dumps(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each entry dumps its own session, options and hashed identity."""
    with freeze_time(FROZEN_NOW):
        first = await setup_entry(hass, monkeypatch)
        second = await setup_entry(
            hass,
            monkeypatch,
            customer_id=OTHER_CUSTOMER_ID,
            meter=EXPORT_METER,
            poll_hours=4,
        )
        first_dump = await async_get_config_entry_diagnostics(hass, first)
        second_dump = await async_get_config_entry_diagnostics(hass, second)

    assert first_dump["entry"]["unique_id"] == customer_hash(CUSTOMER_ID)
    assert second_dump["entry"]["unique_id"] == customer_hash(OTHER_CUSTOMER_ID)
    assert first_dump["entry"]["unique_id"] != second_dump["entry"]["unique_id"]
    assert first_dump["entry"]["options"][CONF_POLL_HOURS] == 2
    assert second_dump["entry"]["options"][CONF_POLL_HOURS] == 4
    assert first_dump["coordinator"]["poll_hours"] == 2
    assert second_dump["coordinator"]["poll_hours"] == 4
    # Neither dump carries the other entry's hash or raw number.
    assert customer_hash(OTHER_CUSTOMER_ID) not in serialized(first_dump)
    assert OTHER_CUSTOMER_ID not in serialized(first_dump)
    assert CUSTOMER_ID not in serialized(second_dump)


async def test_an_entry_without_data_reports_the_absent_parts(hass: HomeAssistant) -> None:
    """An entry that never polled dumps as "absent", not as a missing key."""
    entry = add_entry(hass, customer_id=OTHER_CUSTOMER_ID, token=False, store_customer_id=False)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["meters"] == []
    assert diagnostics["coordinator"] == {
        "last_update_success": False,
        "last_exception": None,
        "last_rate_limit": None,
        "poll_hours": 2,
    }
    assert diagnostics["statistics"] == {
        "done": False,
        "running": False,
        "days": None,
        "next_start": None,
        "chunks": {"done": 0, "total": 0},
    }
    assert diagnostics["session"] == {
        "has_access_token": False,
        "has_refresh_token": False,
        "expires_at": None,
        "expired": None,
    }
    # No customer id in the data: the unique id is the number, and it is hashed
    # like the configured one.
    assert diagnostics["entry"]["unique_id"] == customer_hash(OTHER_CUSTOMER_ID)


async def test_a_failed_poll_reports_the_exception_type_only(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed poll names the exception class and nothing of its message."""
    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch)
        coordinator = entry.runtime_data.coordinator
        coordinator.last_update_success = False
        coordinator.last_exception = TimeoutError("https://secret.example.invalid/x")
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["coordinator"]["last_update_success"] is False
    assert diagnostics["coordinator"]["last_exception"] == "TimeoutError"
    assert "secret.example.invalid" not in serialized(diagnostics)


def test_a_meter_without_customer_metadata_still_has_a_row() -> None:
    """A delivery point the customer payload omits is reported with empty metadata."""
    data = CkwData(
        meters={
            IMPORT_METER: MeterTotals(
                kwh_total=1.0,
                kwh_ht=1.0,
                kwh_nt=0.0,
                last_interval_end=None,
                publication_delay_h=None,
                intervals_seen=1,
                quality_intervals=1,
            )
        },
        rate_limit=None,
        newest_interval=None,
    )

    assert _meters_report(data) == [
        {
            "deliverypoint": mask_deliverypoint(IMPORT_METER),
            "model": None,
            "type": None,
            "fuel": None,
            "last_interval_end": None,
            "publication_delay_h": None,
            "intervals_seen": 1,
            "quality_intervals": 1,
            "present": False,
            "stale_since": None,
        }
    ]
    assert _meters_report(None) == []


def test_the_helpers_keep_their_absent_cases() -> None:
    """No rate limit and no customer number are reported, not guessed."""
    assert _rate_limit_report(None) is None
    assert _redact_title("CKW home", "", "abc123") == "CKW home"


# ---------------------------------------------------------------------------
# The safety pass under the whole dump
# ---------------------------------------------------------------------------


def test_the_last_pass_drops_a_value_under_a_secret_key() -> None:
    """A field added later cannot leak a credential, whatever its depth."""
    dump = {
        "session": {"has_access_token": True, "access_token": ACCESS_TOKEN},
        "nested": [{"refresh_token": REFRESH_TOKEN, "kept": 1}, {"authorization": "Bearer x"}],
        "tokens": [REFRESH_TOKEN],
        "password": "hunter2",
        "secret": {"value": "x"},
        "note": "kept",
    }

    redacted = _redact_secrets(dump)

    assert redacted == {
        "session": {"has_access_token": True},
        "nested": [{"kept": 1}, {}],
        "note": "kept",
    }
    text = serialized(redacted)
    assert TOKEN_PATTERN.search(text) is None
    assert "hunter2" not in text
    assert "Bearer" not in text
