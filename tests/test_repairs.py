"""Tests for the repair issues (Gold rule ``repair-issues``, #133, #157).

The four checks are exercised through the real issue registry: each condition
must raise exactly one issue with a translation key, a severity and the
placeholders ``strings.json`` documents, and must delete that issue again on the
poll that no longer sees the condition.

No socket is opened and no platform is set up: the entry carries a coordinator
stand-in with the two members the checks read (``deliverypoint`` and
``intervals``), and the poll test builds the real ``CkwCoordinator`` around a
fake client.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
from freezegun import freeze_time
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ckw import coordinator as coordinator_module
from custom_components.ckw.api import (
    CkwCustomer,
    CkwMeasurement,
    CkwMeter,
    CkwMeterMeasurements,
    mask_deliverypoint,
)
from custom_components.ckw.const import (
    CONF_CUSTOMER_ID,
    CONF_METER,
    DATA_QUALITY_FLAG_INTERVALS,
    DOCS_QUARTER_HOUR_RETENTION_URL,
    DOMAIN,
    ISSUE_DATA_QUALITY,
    ISSUE_METER_EXCHANGED,
    ISSUE_QUARTER_HOUR_RETENTION,
    ISSUE_STALE_PUBLICATION,
    PUBLICATION_STALL_DAYS,
    QUARTER_HOUR_ENTITY_KEY,
    QUARTER_HOUR_MIN_RETENTION_DAYS,
    ROLLING_WINDOW_DAYS,
    TIME_ZONE,
    Fuel,
    MeterModel,
    MeterType,
)
from custom_components.ckw.coordinator import CkwCoordinator, CkwData, MeterTotals
from custom_components.ckw.repairs import async_check_repairs

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "hass_storage")

COMPONENT_DIR: Final[Path] = Path("custom_components/ckw")
PLACEHOLDER: Final = re.compile(r"\{([a-z_]+)\}")

METER: Final = "CH1003601234500000000000000000000"
CUSTOMER_ID: Final = "0000000099"
#: Frozen poll time, winter (CET): 2026-11-02 12:00 local, 11:00 UTC.
FROZEN_NOW: Final = datetime(2026, 11, 2, 12, 0, tzinfo=TIME_ZONE)


# ---------------------------------------------------------------------------
# A coordinator stand-in, an entry and the data of one poll
# ---------------------------------------------------------------------------


class FakeCoordinator:
    """The two coordinator members the repair checks read."""

    def __init__(self, deliverypoint: str = METER) -> None:
        """Bind the stand-in to one delivery point with no intervals yet."""
        self.deliverypoint = deliverypoint
        self.intervals_by_meter: dict[str, tuple[CkwMeasurement, ...]] = {}

    def intervals(self, deliverypoint: str) -> tuple[CkwMeasurement, ...]:
        """Return the daily intervals this stand-in was given."""
        return self.intervals_by_meter.get(deliverypoint, ())


def add_entry(hass: HomeAssistant, customer_id: str = CUSTOMER_ID) -> MockConfigEntry:
    """Add a CKW config entry whose runtime data carries the stand-in."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CUSTOMER_ID: customer_id, CONF_METER: METER},
        options={},
        title=f"{customer_id} — Luzern",
        unique_id=customer_id,
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        api=None,
        coordinator=FakeCoordinator(),
        statistics_state=None,
    )
    return entry


def make_data(
    *,
    supply_start: str | None = "2019-04-01",
    newest: datetime | None = None,
    polled_at: datetime = FROZEN_NOW,
) -> CkwData:
    """Build one successful poll's data for the entry's delivery point."""
    meter = CkwMeter(
        id="meter-1",
        model=MeterModel.SMART,
        type=MeterType.IMPORT,
        fuel=Fuel.ELEC,
        deliverypointnumber=METER,
        supply_start=supply_start,
        supply_end=None,
    )
    totals = MeterTotals(
        kwh_total=12.0,
        kwh_ht=8.0,
        kwh_nt=4.0,
        last_interval_end=newest,
        publication_delay_h=None,
        intervals_seen=4,
        quality_intervals=0,
    )
    return CkwData(
        meters={METER: totals},
        rate_limit=None,
        newest_interval=newest,
        meter_info={METER: meter},
        polled_at=polled_at,
    )


def daily(day: date, *, flags: int = 1) -> CkwMeasurement:
    """Build one daily interval covering ``day`` in ``Europe/Zurich``.

    ``flags`` above ``1`` sets every ``anzahl_linien_*`` counter, which is what
    ``CkwMeasurement.has_quality_issue`` looks at.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=TIME_ZONE)
    return CkwMeasurement(
        start_utc=start.astimezone(UTC),
        end_utc=(start + timedelta(days=1)).astimezone(UTC),
        kwh_ht=2.0,
        kwh_nt=1.0,
        chf_ht=None,
        chf_nt=None,
        lines_p=flags,
        lines_fw=flags,
        lines_fb=flags,
    )


def issues_of(registry: ir.IssueRegistry) -> list[ir.IssueEntry]:
    """Return every issue this integration raised, whatever the entry."""
    return [issue for (domain, _id), issue in registry.issues.items() if domain == DOMAIN]


def issue_id(entry: MockConfigEntry, condition: str) -> str:
    """Return the namespaced issue id of one condition of one entry."""
    return f"{DOMAIN}_{entry.entry_id}_{condition}"


def register_quarter_hour(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Register the quarter-hour sensor entity of one entry."""
    er.async_get(hass).async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{entry.entry_id}_{METER}_{QUARTER_HOUR_ENTITY_KEY}",
    )


# ---------------------------------------------------------------------------
# 1. Meter exchange
# ---------------------------------------------------------------------------


async def test_a_meter_exchange_raises_one_warning_issue(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A changed supply start date is reported once, with the masked point and the date."""
    entry = add_entry(hass)

    await async_check_repairs(hass, entry, make_data(supply_start="2019-04-01"))
    assert issues_of(issue_registry) == []

    await async_check_repairs(hass, entry, make_data(supply_start="2026-05-01"))

    issues = issues_of(issue_registry)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.issue_id == issue_id(entry, ISSUE_METER_EXCHANGED)
    assert issue.translation_key == ISSUE_METER_EXCHANGED
    assert issue.severity is IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {
        "deliverypoint": mask_deliverypoint(METER),
        "date": "2026-05-01",
    }


async def test_the_meter_issue_is_cleared_when_the_date_matches_again(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """CKW correcting the date back clears the issue like every other condition."""
    entry = add_entry(hass)

    await async_check_repairs(hass, entry, make_data(supply_start="2019-04-01"))
    await async_check_repairs(hass, entry, make_data(supply_start="2026-05-01"))
    assert len(issues_of(issue_registry)) == 1

    await async_check_repairs(hass, entry, make_data(supply_start="2019-04-01"))
    assert issues_of(issue_registry) == []


async def test_a_meter_without_a_supply_start_raises_nothing(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """No date in the payload means nothing to compare, so nothing is reported."""
    entry = add_entry(hass)

    await async_check_repairs(hass, entry, make_data(supply_start=None))
    await async_check_repairs(hass, entry, make_data(supply_start=None))

    assert issues_of(issue_registry) == []


# ---------------------------------------------------------------------------
# 2. Persistent quality flags
# ---------------------------------------------------------------------------


async def test_persistent_quality_flags_raise_and_clear_the_issue(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """More than the threshold of flagged intervals raises; no flag left clears."""
    entry = add_entry(hass)
    coordinator: FakeCoordinator = entry.runtime_data.coordinator
    flagged = [
        daily(date(2026, 10, 30) + timedelta(days=offset), flags=2)
        for offset in range(DATA_QUALITY_FLAG_INTERVALS + 1)
    ]
    coordinator.intervals_by_meter[METER] = tuple(flagged)

    await async_check_repairs(hass, entry, make_data())

    issues = issues_of(issue_registry)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.issue_id == issue_id(entry, ISSUE_DATA_QUALITY)
    assert issue.translation_key == ISSUE_DATA_QUALITY
    assert issue.severity is IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {
        "deliverypoint": mask_deliverypoint(METER),
        "count": str(DATA_QUALITY_FLAG_INTERVALS + 1),
        "days": str(ROLLING_WINDOW_DAYS),
    }

    # The flags are gone: the issue goes with them.
    coordinator.intervals_by_meter[METER] = (daily(date(2026, 11, 1)),)
    await async_check_repairs(hass, entry, make_data())
    assert issues_of(issue_registry) == []


async def test_flags_inside_the_threshold_raise_nothing(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Two or three flagged intervals are noise, not a repair issue."""
    entry = add_entry(hass)
    coordinator: FakeCoordinator = entry.runtime_data.coordinator
    coordinator.intervals_by_meter[METER] = tuple(
        daily(date(2026, 10, 30) + timedelta(days=offset), flags=2)
        for offset in range(DATA_QUALITY_FLAG_INTERVALS)
    )

    await async_check_repairs(hass, entry, make_data())

    assert issues_of(issue_registry) == []


async def test_flags_outside_the_rolling_window_are_ignored(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An old flagged day does not keep the repair dashboard occupied forever."""
    entry = add_entry(hass)
    coordinator: FakeCoordinator = entry.runtime_data.coordinator
    coordinator.intervals_by_meter[METER] = tuple(
        daily(date(2026, 9, 1) + timedelta(days=offset), flags=2)
        for offset in range(DATA_QUALITY_FLAG_INTERVALS + 1)
    )

    await async_check_repairs(hass, entry, make_data())

    assert issues_of(issue_registry) == []


# ---------------------------------------------------------------------------
# 3. A stalled publication
# ---------------------------------------------------------------------------


async def test_a_stalled_publication_raises_and_clears_the_issue(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A newest interval older than the stall threshold raises; a newer one clears."""
    entry = add_entry(hass)
    stale = FROZEN_NOW - timedelta(days=PUBLICATION_STALL_DAYS + 1)

    await async_check_repairs(hass, entry, make_data(newest=stale))

    issues = issues_of(issue_registry)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.issue_id == issue_id(entry, ISSUE_STALE_PUBLICATION)
    assert issue.translation_key == ISSUE_STALE_PUBLICATION
    assert issue.severity is IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {
        "deliverypoint": mask_deliverypoint(METER),
        "date": stale.astimezone(TIME_ZONE).date().isoformat(),
        "days": str(PUBLICATION_STALL_DAYS),
    }

    # CKW publishes again: the newest interval moves forward and the issue goes.
    await async_check_repairs(hass, entry, make_data(newest=FROZEN_NOW - timedelta(hours=30)))
    assert issues_of(issue_registry) == []


async def test_the_normal_publication_lag_raises_nothing(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """One to three days of lag is the expected behaviour, not a repair issue."""
    entry = add_entry(hass)

    await async_check_repairs(hass, entry, make_data(newest=FROZEN_NOW - timedelta(days=3)))

    assert issues_of(issue_registry) == []


# ---------------------------------------------------------------------------
# 4. Recorder retention below the recommendation
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("recorder_mock")
class TestQuarterHourRetention:
    """The retention check reads the recorder, so these two need a real one."""

    async def test_the_quarter_hour_retention_issue(
        self,
        recorder_mock: Any,
        hass: HomeAssistant,
        issue_registry: ir.IssueRegistry,
    ) -> None:
        """An enabled quarter-hour sensor plus a short retention raises one issue."""
        entry = add_entry(hass)
        register_quarter_hour(hass, entry)

        await async_check_repairs(hass, entry, make_data())

        issues = issues_of(issue_registry)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.issue_id == issue_id(entry, ISSUE_QUARTER_HOUR_RETENTION)
        assert issue.translation_key == ISSUE_QUARTER_HOUR_RETENTION
        assert issue.severity is IssueSeverity.WARNING
        assert issue.is_fixable is False
        assert issue.learn_more_url == DOCS_QUARTER_HOUR_RETENTION_URL
        assert issue.translation_placeholders == {
            "deliverypoint": mask_deliverypoint(METER),
            "days": str(recorder_mock.keep_days),
            "minimum": str(QUARTER_HOUR_MIN_RETENTION_DAYS),
        }
        assert recorder_mock.keep_days < QUARTER_HOUR_MIN_RETENTION_DAYS

        # The user raises the retention: the issue disappears on the next poll.
        recorder_mock.keep_days = QUARTER_HOUR_MIN_RETENTION_DAYS
        await async_check_repairs(hass, entry, make_data())
        assert issues_of(issue_registry) == []

    async def test_a_disabled_quarter_hour_sensor_raises_nothing(
        self,
        recorder_mock: Any,
        hass: HomeAssistant,
        issue_registry: ir.IssueRegistry,
    ) -> None:
        """Only a user who enabled the sensor needs to hear about the retention."""
        entry = add_entry(hass)
        assert recorder_mock.keep_days < QUARTER_HOUR_MIN_RETENTION_DAYS

        await async_check_repairs(hass, entry, make_data())

        assert issues_of(issue_registry) == []


async def test_a_missing_recorder_config_does_not_crash_the_check(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Without a recorder the retention is unknown, so the check is skipped."""
    entry = add_entry(hass)
    register_quarter_hour(hass, entry)
    assert "recorder" not in hass.config.components

    await async_check_repairs(hass, entry, make_data())

    assert issues_of(issue_registry) == []


# ---------------------------------------------------------------------------
# Two accounts, and repeated polls
# ---------------------------------------------------------------------------


async def test_two_entries_produce_two_independent_issue_ids(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Each entry owns its issue id, so one account never overwrites the other."""
    first = add_entry(hass, customer_id="0000000099")
    second = add_entry(hass, customer_id="0000000100")
    stale = FROZEN_NOW - timedelta(days=PUBLICATION_STALL_DAYS + 1)

    await async_check_repairs(hass, first, make_data(newest=stale))
    await async_check_repairs(hass, second, make_data(newest=stale))

    ids = {issue.issue_id for issue in issues_of(issue_registry)}
    assert ids == {
        f"{DOMAIN}_{first.entry_id}_{ISSUE_STALE_PUBLICATION}",
        f"{DOMAIN}_{second.entry_id}_{ISSUE_STALE_PUBLICATION}",
    }


async def test_three_checks_in_a_row_keep_one_issue(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A condition that persists is never re-raised as a second issue."""
    entry = add_entry(hass)
    coordinator: FakeCoordinator = entry.runtime_data.coordinator
    coordinator.intervals_by_meter[METER] = tuple(
        daily(date(2026, 10, 30) + timedelta(days=offset), flags=2)
        for offset in range(DATA_QUALITY_FLAG_INTERVALS + 1)
    )
    register_quarter_hour(hass, entry)

    for _ in range(3):
        await async_check_repairs(hass, entry, make_data(supply_start="2019-04-01"))

    issues = issues_of(issue_registry)
    assert [issue.issue_id for issue in issues] == [issue_id(entry, ISSUE_DATA_QUALITY)]
    # The retention check ran on the same poll and skipped: no recorder is set up.
    assert ISSUE_QUARTER_HOUR_RETENTION not in {issue.translation_key for issue in issues}


# ---------------------------------------------------------------------------
# The coordinator calls the check after every successful poll
# ---------------------------------------------------------------------------


class FakePollClient:
    """Answer the coordinator's requests without opening a socket."""

    def __init__(self) -> None:
        """Start with the headers of no response."""
        self.last_rate_limit = None
        self.calls: list[str] = []

    async def async_get_customer(self) -> CkwCustomer | None:
        """Return no meter list: the metadata is not what this test checks."""
        return None

    async def async_get_measurements(
        self,
        start: date,
        end: date,
        aggregation: str = "days",
    ) -> list[CkwMeterMeasurements]:
        """Return four flagged days for the daily window and nothing for minutes."""
        self.calls.append(aggregation)
        if aggregation != "days":
            return []
        days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        flagged = [daily(day, flags=2) for day in days[-4:]]
        return [CkwMeterMeasurements(deliverypointnumber=METER, measurements=tuple(flagged))]


async def test_the_coordinator_checks_repairs_after_a_poll(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A poll whose data carries flags leaves the issue in the registry."""
    entry = add_entry(hass)
    coordinator = CkwCoordinator(hass, entry, FakePollClient())  # type: ignore[arg-type]
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    with freeze_time(FROZEN_NOW):
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    keys = {issue.translation_key for issue in issues_of(issue_registry)}
    assert keys == {ISSUE_DATA_QUALITY}


async def test_a_failing_repair_check_never_fails_the_poll(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The data matters more than the issue: a broken check is logged, not raised."""
    entry = add_entry(hass)
    coordinator = CkwCoordinator(hass, entry, FakePollClient())  # type: ignore[arg-type]
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("repair check broken")

    monkeypatch.setattr(coordinator_module, "async_check_repairs", boom)

    with freeze_time(FROZEN_NOW):
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert issues_of(issue_registry) == []


# ---------------------------------------------------------------------------
# Strings: every issue has a translation key in every language
# ---------------------------------------------------------------------------


def load(relative: str) -> dict[str, Any]:
    """Read one JSON file of the component."""
    return json.loads((COMPONENT_DIR / relative).read_text(encoding="utf-8"))


def placeholders(text: str) -> set[str]:
    """Return the placeholder names a message carries."""
    return set(PLACEHOLDER.findall(text))


def test_strings_json_documents_every_issue() -> None:
    """All four conditions have a title and a description with placeholders."""
    issues: dict[str, Any] = load("strings.json")["issues"]

    assert set(issues) == {
        ISSUE_METER_EXCHANGED,
        ISSUE_DATA_QUALITY,
        ISSUE_STALE_PUBLICATION,
        ISSUE_QUARTER_HOUR_RETENTION,
    }
    for key, entry in issues.items():
        assert entry["title"].strip(), key
        assert entry["description"].strip(), key
        assert placeholders(entry["title"]) | placeholders(entry["description"])


@pytest.mark.parametrize("language", ["en", "de"])
def test_both_translations_carry_every_issue(language: str) -> None:
    """English and German mirror ``strings.json``, placeholders included."""
    source: Mapping[str, Any] = load("strings.json")["issues"]
    translated: Mapping[str, Any] = load(f"translations/{language}.json")["issues"]

    assert set(translated) == set(source)
    for key, entry in source.items():
        for field in ("title", "description"):
            expected = placeholders(entry[field])
            actual = placeholders(translated[key][field])
            assert actual == expected, f"{language}.json {key}.{field} placeholders"
            assert translated[key][field].strip()
