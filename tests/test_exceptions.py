"""Tests for the translated user-facing errors (Gold rule ``exception-translations``, #130).

Two things are asserted here and nothing else: every error class a service can
raise carries a translation key that exists in ``strings.json``, and the keys
stay mirrored in both shipped languages. The service tests call ``ckw.backfill``
against a fake client, so a failure reaches the caller as a
``HomeAssistantError`` instead of a raw ``aiohttp`` exception or a log line.

Translations are asserted as keys, never as English sentences: a wording change
must not turn a test red, a missing key must (``docs/reference/conventions.md``,
"Error mapping").
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final
from unittest.mock import Mock

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ckw.api import (
    CkwApiError,
    CkwAuthError,
    CkwConnectionError,
    CkwError,
    CkwMeter,
    CkwMeterMeasurements,
    CkwRateLimitError,
)
from custom_components.ckw.api import (
    CkwRangeTooBigError as CkwRangeCapError,
)
from custom_components.ckw.const import (
    ATTR_DAYS,
    ATTR_ENTRY_ID,
    DAYS_AGGREGATION,
    DOMAIN,
    SERVICE_BACKFILL,
    Fuel,
    MeterModel,
    MeterType,
)
from custom_components.ckw.exceptions import (
    CkwApiStatusError,
    CkwAuthenticationError,
    CkwConnectionFailedError,
    CkwException,
    CkwNoDataError,
    CkwNoMetersError,
    CkwRangeTooBigError,
    CkwRateLimitedError,
    CkwUnknownError,
    error_for_api_failure,
)
from custom_components.ckw.statistics import StatisticsState, async_register_backfill_service

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

COMPONENT_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "custom_components" / "ckw"
METER: Final = "CH0000000000000000000000000000001"
PLACEHOLDER: Final = re.compile(r"{(\w+)}")


#: One instance per translated error; a new class belongs in this list.
TRANSLATED_ERRORS: Final[tuple[CkwException, ...]] = (
    CkwAuthenticationError(),
    CkwRateLimitedError(120.0),
    CkwRateLimitedError(),
    CkwRangeTooBigError(),
    CkwApiStatusError(503),
    CkwNoMetersError(),
    CkwNoDataError(),
    CkwConnectionFailedError(),
    CkwUnknownError(),
)


def _load(relative: str) -> dict[str, Any]:
    """Read one JSON file of the component."""
    path = COMPONENT_DIR / relative
    return json.loads(path.read_text(encoding="utf-8"))


def _placeholders(message: str) -> set[str]:
    """Return the placeholder names a message carries."""
    return set(PLACEHOLDER.findall(message))


def test_every_exception_carries_a_key_that_strings_json_defines() -> None:
    """Each class names its condition, and ``strings.json`` knows the name."""
    exceptions: dict[str, Any] = _load("strings.json")["exceptions"]

    for err in TRANSLATED_ERRORS:
        assert isinstance(err, HomeAssistantError)
        assert err.translation_domain == DOMAIN
        assert err.translation_key is not None
        assert err.translation_key in exceptions, err.translation_key
        message: str = exceptions[err.translation_key]["message"]
        assert _placeholders(message) == set(err.translation_placeholders or {})


def test_rate_limit_exception_renders_the_reset_seconds() -> None:
    """A known reset becomes the ``reset`` placeholder; an unknown one is a key."""
    err = CkwRateLimitedError(120.0)

    assert err.translation_key == "rate_limited"
    assert err.translation_placeholders == {"reset": "120 s"}
    assert "120" in err.translation_placeholders["reset"]

    unknown = CkwRateLimitedError(None)
    assert unknown.translation_key == "rate_limited_no_reset"
    assert unknown.translation_placeholders is None


@pytest.mark.parametrize(
    ("api_error", "translation_key"),
    [
        (CkwAuthError("bad token"), "invalid_auth"),
        (CkwRateLimitError("quota", retry_after_seconds=30.0), "rate_limited"),
        (CkwRateLimitError("quota"), "rate_limited_no_reset"),
        (CkwRangeCapError(400, "response too big"), "range_too_big"),
        (CkwApiError(503, "busy"), "api_error"),
        (CkwConnectionError("offline"), "cannot_connect"),
        (CkwError("something new"), "unknown"),
    ],
)
def test_api_failures_map_to_semantic_keys(api_error: CkwError, translation_key: str) -> None:
    """The one mapping keeps the HTTP status out of the translation key."""
    err = error_for_api_failure(api_error)

    assert isinstance(err, CkwException)
    assert err.translation_key == translation_key


def test_both_translations_carry_every_exception_key() -> None:
    """English and German mirror ``strings.json``, placeholders included."""
    source: dict[str, Any] = _load("strings.json")["exceptions"]

    for language in ("en", "de"):
        translated: dict[str, Any] = _load(f"translations/{language}.json")["exceptions"]
        missing = sorted(set(source) - set(translated))
        extra = sorted(set(translated) - set(source))
        assert not missing, f"{language}.json misses {missing}"
        assert not extra, f"{language}.json carries stale keys {extra}"
        for key, entry in source.items():
            expected = _placeholders(entry["message"])
            actual = _placeholders(translated[key]["message"])
            assert actual == expected, f"{language}.json {key} placeholders"
            assert translated[key]["message"].strip()


# ---------------------------------------------------------------------------
# The `ckw.backfill` service as the user-facing error surface
# ---------------------------------------------------------------------------


class FakeClient:
    """Answer ``async_get_measurements`` with one configured outcome."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        series: list[CkwMeterMeasurements] | None = None,
    ) -> None:
        self.error = error
        self.series = series or []
        self.calls: list[tuple[date, date, str]] = []

    async def async_get_measurements(
        self,
        start: date,
        end: date,
        aggregation: str = DAYS_AGGREGATION,
    ) -> list[CkwMeterMeasurements]:
        """Record the chunk and raise or replay the configured answer."""
        self.calls.append((start, end, aggregation))
        if self.error is not None:
            raise self.error
        return self.series


class FakeCoordinator:
    """The coordinator members the backfill reads, with the fake client."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        series: list[CkwMeterMeasurements] | None = None,
        meter_info: dict[str, CkwMeter] | None = None,
    ) -> None:
        self.client = FakeClient(error=error, series=series)
        self.data = SimpleNamespace(meters={}, meter_info=meter_info or {})
        self.paced = 0

    async def async_pace(self) -> None:
        """Count the pacing calls instead of waiting for a quota window."""
        self.paced += 1


async def _loaded_entry(hass: HomeAssistant, coordinator: FakeCoordinator) -> MockConfigEntry:
    """Add the entry the service repairs, in the state a running setup leaves."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
        title="CKW exception test",
        unique_id="exception-service",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        api=None,
        coordinator=coordinator,
        statistics_state=StatisticsState(),
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


async def _call_backfill(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Register the service and call it for one day of history."""
    async_register_backfill_service(hass)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_BACKFILL,
        {ATTR_ENTRY_ID: entry.entry_id, ATTR_DAYS: 1},
        blocking=True,
    )


async def test_backfill_service_raises_the_translated_auth_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``401`` reaches the caller as ``invalid_auth``, not as a log line."""
    coordinator = FakeCoordinator(error=CkwAuthError("Bad or expired token"))
    entry = await _loaded_entry(hass, coordinator)
    monkeypatch.setattr(entry, "async_start_reauth", Mock())

    with pytest.raises(CkwAuthenticationError) as raised:
        await _call_backfill(hass, entry)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "invalid_auth"
    assert raised.value.__cause__ is coordinator.client.error


async def test_backfill_service_translates_a_raw_aiohttp_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed token refresh answers with ``aiohttp``: the UI must still see a key."""
    raw = aiohttp.ClientResponseError(Mock(), (), status=401)
    entry = await _loaded_entry(hass, FakeCoordinator(error=raw))
    monkeypatch.setattr(entry, "async_start_reauth", Mock())

    with pytest.raises(HomeAssistantError) as raised:
        await _call_backfill(hass, entry)

    assert not isinstance(raised.value, aiohttp.ClientError)
    assert raised.value.translation_key == "invalid_auth"


async def test_backfill_service_reports_an_empty_history_as_no_data(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API answered, but nothing was importable: the service says so."""
    entry = await _loaded_entry(hass, FakeCoordinator(series=[]))
    monkeypatch.setattr(entry, "async_start_reauth", Mock())

    with pytest.raises(CkwNoDataError) as raised:
        await _call_backfill(hass, entry)

    assert raised.value.translation_key == "no_data"


async def test_backfill_service_reports_a_gas_only_answer_as_no_meters(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data for a meter the integration does not read is its own condition."""
    gas_meter = CkwMeter(
        id="gas",
        model=MeterModel.SMART,
        type=MeterType.CONSUMPTION,
        fuel=Fuel.GAS,
        deliverypointnumber=METER,
        supply_start=None,
        supply_end=None,
    )
    series = [CkwMeterMeasurements(deliverypointnumber=METER, measurements=())]
    entry = await _loaded_entry(hass, FakeCoordinator(series=series, meter_info={METER: gas_meter}))
    monkeypatch.setattr(entry, "async_start_reauth", Mock())

    with pytest.raises(CkwNoMetersError) as raised:
        await _call_backfill(hass, entry)

    assert raised.value.translation_key == "no_meters"
