"""Deterministically regenerate the measurement fixtures.

The mock API (``scripts/dev/mock/app.py``) and the fixture mode of the integration
(#39) both read the committed fixtures, so this script is only the rebuild path: the
JSON is never hand-edited.

Two files are written:

``tests/fixtures/measurements_days.json``
    40 consecutive days, 2026-09-22 .. 2026-10-31, one daily entry per meter.
    Day boundaries are Europe/Zurich midnights expressed in UTC, so 2026-10-25
    (DST ends) is a 25 hour day: ``2026-10-24T22:00:00`` .. ``2026-10-25T23:00:00``.
    Exactly one entry (the electricity meter on 2026-10-02) carries
    ``anzahl_linien_p == 2``; every other entry carries 1. ``anzahl_linien_fw`` and
    ``anzahl_linien_fb`` are 1 everywhere, as the deployed API model shows - they are
    not ``0``. 10-25 kWh/day split roughly 60/40 into HT/NT at 0.24/0.19 CHF per kWh.

``tests/fixtures/measurements_minutes.json``
    One day, 2026-10-01, in 96 quarter-hour intervals per meter - CKW's finest
    documented resolution (``minute``/``minutes`` mean "finest available", not one
    minute). HT follows the Swiss tariff window (Mon-Fri 07:00-20:00, Sat
    07:00-13:00, Sun never), so the HT/NT split is driven by the clock rather than a
    fixed share. Every entry carries ``anzahl_linien_p/fw/fb == 1``.

The meters come from ``tests/fixtures/customer.json`` (all three: electricity import,
PV export and gas), and a fixed seed makes every run byte-identical. The second
meter is a PV feed-in meter and would be paid the feed-in tariff in reality; the mock
deliberately uses the same energy band and prices for every meter, because consumers
of the fixture only need plausible, obviously fake numbers.

Run it with::

    mise exec -- python scripts/dev/mock/generate_fixtures.py
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
FIXTURE_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"
CUSTOMER_FIXTURE: Final[Path] = FIXTURE_DIR / "customer.json"
MEASUREMENTS_DAYS_FIXTURE: Final[Path] = FIXTURE_DIR / "measurements_days.json"
MEASUREMENTS_MINUTES_FIXTURE: Final[Path] = FIXTURE_DIR / "measurements_minutes.json"

FIRST_DAY: Final[date] = date(2026, 9, 22)
LAST_DAY: Final[date] = date(2026, 10, 31)
MINUTE_DAY: Final[date] = date(2026, 10, 1)
SEED: Final[int] = 20260922

PRICE_HT: Final[float] = 0.24
PRICE_NT: Final[float] = 0.19
MIN_KWH_PER_DAY: Final[float] = 10.0
MAX_KWH_PER_DAY: Final[float] = 25.0
HT_SHARE_MIN: Final[float] = 0.55
HT_SHARE_MAX: Final[float] = 0.65

QUARTER_HOUR: Final[timedelta] = timedelta(minutes=15)
SLOTS_PER_DAY: Final[int] = 24 * 4

QUALITY_FLAG_METER_INDEX: Final[int] = 0
QUALITY_FLAG_DAY: Final[date] = date(2026, 10, 2)

ZURICH: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")
STAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"


def _stamp(moment: datetime) -> str:
    """Format a UTC moment the way the CKW API does (no timezone suffix)."""
    return moment.astimezone(UTC).strftime(STAMP_FORMAT)


def _local_midnight(day: date) -> datetime:
    """Return local (Europe/Zurich) midnight of ``day``."""
    return datetime.combine(day, time(0, 0), tzinfo=ZURICH)


def _day_bounds(day: date) -> tuple[str, str]:
    """Return the UTC start and end of a local calendar day."""
    return _stamp(_local_midnight(day)), _stamp(_local_midnight(day + timedelta(1)))


def _delivery_point_numbers() -> list[str]:
    """Read every meter from the customer fixture so both files stay in sync."""
    payload: dict[str, Any] = json.loads(CUSTOMER_FIXTURE.read_text(encoding="utf-8"))
    users: list[dict[str, Any]] = payload["customer"]["User"]
    numbers: list[str] = []
    for user in users:
        for location in user["locations"]:
            numbers.extend(
                str(meter["deliverypointnumber"]) for meter in location["meters"]
            )
    return numbers


def _daily_entry(
    rng: random.Random, day: date, *, quality_flag: bool
) -> dict[str, Any]:
    """Build one daily measurement row for one meter."""
    total_kwh = rng.uniform(MIN_KWH_PER_DAY, MAX_KWH_PER_DAY)
    quantity_ht = round(total_kwh * rng.uniform(HT_SHARE_MIN, HT_SHARE_MAX), 1)
    quantity_nt = round(total_kwh - quantity_ht, 1)
    von, bis = _day_bounds(day)
    return {
        "zeitstempel_von_utc": von,
        "zeitstempel_bis_utc": bis,
        "betrag_ht": round(quantity_ht * PRICE_HT, 2),
        "betrag_nt": round(quantity_nt * PRICE_NT, 2),
        "menge_fakturiert_ht": quantity_ht,
        "menge_fakturiert_nt": quantity_nt,
        "anzahl_linien_p": 2 if quality_flag else 1,
        "anzahl_linien_fw": 1,
        "anzahl_linien_fb": 1,
    }


def _is_high_tariff(moment: datetime) -> bool:
    """Swiss HT window: Mon-Fri 07:00-20:00, Sat 07:00-13:00, Sun never."""
    weekday = moment.weekday()
    if weekday == 6:
        return False
    if weekday == 5:
        return 7 <= moment.hour < 13
    return 7 <= moment.hour < 20


def _minute_entries(rng: random.Random, day: date) -> list[dict[str, Any]]:
    """Build 96 quarter-hour rows for one meter and one local day."""
    day_total = rng.uniform(MIN_KWH_PER_DAY, MAX_KWH_PER_DAY)
    entries: list[dict[str, Any]] = []
    for slot in range(SLOTS_PER_DAY):
        von = _local_midnight(day) + slot * QUARTER_HOUR
        bis = von + QUARTER_HOUR
        quantity = round(day_total / SLOTS_PER_DAY * rng.uniform(0.85, 1.15), 3)
        high = _is_high_tariff(von)
        entries.append(
            {
                "zeitstempel_von_utc": _stamp(von),
                "zeitstempel_bis_utc": _stamp(bis),
                "betrag_ht": round(quantity * PRICE_HT, 2) if high else 0.0,
                "betrag_nt": round(quantity * PRICE_NT, 2) if not high else 0.0,
                "menge_fakturiert_ht": quantity if high else 0.0,
                "menge_fakturiert_nt": 0.0 if high else quantity,
                "anzahl_linien_p": 1,
                "anzahl_linien_fw": 1,
                "anzahl_linien_fb": 1,
            }
        )
    return entries


def build_days() -> dict[str, Any]:
    """Build the daily fixture document."""
    rng = random.Random(SEED)
    days = [
        FIRST_DAY + timedelta(offset)
        for offset in range((LAST_DAY - FIRST_DAY).days + 1)
    ]
    series: dict[str, list[dict[str, Any]]] = {}
    for index, deliverypointnumber in enumerate(_delivery_point_numbers()):
        series[deliverypointnumber] = [
            _daily_entry(
                rng,
                day,
                quality_flag=index == QUALITY_FLAG_METER_INDEX
                and day == QUALITY_FLAG_DAY,
            )
            for day in days
        ]
    return {
        "aggregation": "day(s)",
        "von": FIRST_DAY.strftime("%Y%m%d"),
        "bis": LAST_DAY.strftime("%Y%m%d"),
        "series": series,
    }


def build_minutes() -> dict[str, Any]:
    """Build the quarter-hour fixture document (one day)."""
    rng = random.Random(SEED + 1)
    series = {
        deliverypointnumber: _minute_entries(rng, MINUTE_DAY)
        for deliverypointnumber in _delivery_point_numbers()
    }
    return {
        "aggregation": "minute(s)",
        "day": MINUTE_DAY.strftime("%Y%m%d"),
        "von": MINUTE_DAY.strftime("%Y%m%d"),
        "bis": MINUTE_DAY.strftime("%Y%m%d"),
        "series": series,
    }


def _validate_days(document: dict[str, Any]) -> None:
    """Fail loudly if a regenerated daily fixture lost one of its guarantees."""
    expected_days = (LAST_DAY - FIRST_DAY).days + 1
    flagged = 0
    for deliverypointnumber, entries in document["series"].items():
        if len(entries) != expected_days:
            raise SystemExit(
                f"{deliverypointnumber}: {len(entries)} entries, expected {expected_days}"
            )
        for entry in entries:
            if entry["anzahl_linien_fw"] != 1 or entry["anzahl_linien_fb"] != 1:
                raise SystemExit(
                    f"{deliverypointnumber}: fw/fb must be 1 everywhere, got "
                    f"{entry['anzahl_linien_fw']}/{entry['anzahl_linien_fb']}"
                )
        flagged += sum(1 for entry in entries if entry["anzahl_linien_p"] != 1)
    if flagged != 1:
        raise SystemExit(f"expected exactly one quality-flagged day, found {flagged}")


def _validate_minutes(document: dict[str, Any]) -> None:
    """Fail loudly if a regenerated quarter-hour fixture lost its guarantees."""
    for deliverypointnumber, entries in document["series"].items():
        if len(entries) != SLOTS_PER_DAY:
            raise SystemExit(
                f"{deliverypointnumber}: {len(entries)} entries, "
                f"expected {SLOTS_PER_DAY}"
            )
        for earlier, later in itertools.pairwise(entries):
            if earlier["zeitstempel_bis_utc"] != later["zeitstempel_von_utc"]:
                raise SystemExit(f"{deliverypointnumber}: intervals are not contiguous")
        for entry in entries:
            von = datetime.strptime(entry["zeitstempel_von_utc"], STAMP_FORMAT)
            bis = datetime.strptime(entry["zeitstempel_bis_utc"], STAMP_FORMAT)
            if bis - von != QUARTER_HOUR:
                raise SystemExit(f"{deliverypointnumber}: interval is not 15 minutes")
            if entry["anzahl_linien_p"] != 1:
                raise SystemExit(f"{deliverypointnumber}: p must be 1 everywhere")


def main(argv: list[str] | None = None) -> None:
    """Write both measurement fixtures."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days-out",
        type=Path,
        default=MEASUREMENTS_DAYS_FIXTURE,
        help=f"daily output file (default: {MEASUREMENTS_DAYS_FIXTURE})",
    )
    parser.add_argument(
        "--minutes-out",
        type=Path,
        default=MEASUREMENTS_MINUTES_FIXTURE,
        help=f"quarter-hour output file (default: {MEASUREMENTS_MINUTES_FIXTURE})",
    )
    args = parser.parse_args(argv)

    days = build_days()
    _validate_days(days)
    args.days_out.write_text(
        json.dumps(days, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    minutes = build_minutes()
    _validate_minutes(minutes)
    args.minutes_out.write_text(
        json.dumps(minutes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    day_entries = sum(len(entries) for entries in days["series"].values())
    minute_entries = sum(len(entries) for entries in minutes["series"].values())
    print(f"wrote {day_entries} entries to {args.days_out}")
    print(f"wrote {minute_entries} entries to {args.minutes_out}")


if __name__ == "__main__":
    main()
