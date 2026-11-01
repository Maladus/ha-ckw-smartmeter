"""Deterministically regenerate ``tests/fixtures/measurements_days.json``.

The mock API (``scripts/dev/mock/app.py``) and the fixture mode of the integration
(#39) both read the committed fixture, so this script is only the rebuild path: the
JSON is never hand-edited.

Deliberate properties of the generated data:

* 40 consecutive days, 2026-09-22 .. 2026-10-31, one entry per day per meter.
* Day boundaries are Europe/Zurich midnights expressed in UTC, so 2026-10-25 (DST
  ends) is a 25 hour day: ``2026-10-24T22:00:00`` .. ``2026-10-25T23:00:00``.
* Exactly one entry (the electricity meter on 2026-10-02) carries
  ``anzahl_linien_p == 2``; every other entry carries 1. ``anzahl_linien_fw`` and
  ``anzahl_linien_fb`` stay 0 - those are non-electricity line counts.
* A fixed seed, so every run produces byte-identical output.

The second meter is a PV feed-in meter and would be paid the feed-in tariff in
reality; the mock deliberately uses the same 10-25 kWh/day band and the same
0.24/0.19 CHF per kWh prices for both meters, because consumers of the fixture only
need plausible, obviously fake numbers.

Run it with::

    mise exec -- python scripts/dev/mock/generate_fixtures.py
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
FIXTURE_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"
CUSTOMER_FIXTURE: Final[Path] = FIXTURE_DIR / "customer.json"
MEASUREMENTS_FIXTURE: Final[Path] = FIXTURE_DIR / "measurements_days.json"

FIRST_DAY: Final[date] = date(2026, 9, 22)
LAST_DAY: Final[date] = date(2026, 10, 31)
SEED: Final[int] = 20260922

PRICE_HT: Final[float] = 0.24
PRICE_NT: Final[float] = 0.19
MIN_KWH_PER_DAY: Final[float] = 10.0
MAX_KWH_PER_DAY: Final[float] = 25.0
HT_SHARE_MIN: Final[float] = 0.55
HT_SHARE_MAX: Final[float] = 0.65

QUALITY_FLAG_METER_INDEX: Final[int] = 0
QUALITY_FLAG_DAY: Final[date] = date(2026, 10, 2)

ZURICH: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")
STAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"


def _stamp(moment: datetime) -> str:
    """Format a UTC moment the way the CKW API does (no timezone suffix)."""
    return moment.astimezone(UTC).strftime(STAMP_FORMAT)


def _day_bounds(day: date) -> tuple[str, str]:
    """Return the UTC start and end of a local (Europe/Zurich) calendar day."""
    start = datetime.combine(day, time(0, 0), tzinfo=ZURICH)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=ZURICH)
    return _stamp(start), _stamp(end)


def _delivery_point_numbers() -> list[str]:
    """Read the meter list from the customer fixture so both files stay in sync."""
    payload: dict[str, Any] = json.loads(CUSTOMER_FIXTURE.read_text(encoding="utf-8"))
    location: dict[str, Any] = payload["customer"]["locations"][0]
    return [str(meter["deliverypointnumber"]) for meter in location["meters"]]


def _entry(rng: random.Random, day: date, *, quality_flag: bool) -> dict[str, Any]:
    """Build one measurement row for one meter and one day."""
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
        "anzahl_linien_fw": 0,
        "anzahl_linien_fb": 0,
    }


def build_measurements() -> dict[str, Any]:
    """Build the complete fixture document."""
    rng = random.Random(SEED)
    days = [
        FIRST_DAY + timedelta(days=offset)
        for offset in range((LAST_DAY - FIRST_DAY).days + 1)
    ]
    series: dict[str, list[dict[str, Any]]] = {}
    for index, deliverypointnumber in enumerate(_delivery_point_numbers()):
        series[deliverypointnumber] = [
            _entry(
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


def _validate(document: dict[str, Any]) -> None:
    """Fail loudly if a regenerated fixture lost one of its guarantees."""
    expected_days = (LAST_DAY - FIRST_DAY).days + 1
    flagged = 0
    for deliverypointnumber, entries in document["series"].items():
        if len(entries) != expected_days:
            raise SystemExit(
                f"{deliverypointnumber}: {len(entries)} entries, expected {expected_days}"
            )
        flagged += sum(1 for entry in entries if entry["anzahl_linien_p"] != 1)
    if flagged != 1:
        raise SystemExit(f"expected exactly one quality-flagged day, found {flagged}")


def main(argv: list[str] | None = None) -> None:
    """Write the measurement fixture."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=MEASUREMENTS_FIXTURE,
        help=f"output file (default: {MEASUREMENTS_FIXTURE})",
    )
    args = parser.parse_args(argv)

    document = build_measurements()
    _validate(document)
    args.out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = sum(len(entries) for entries in document["series"].values())
    print(f"wrote {total} entries to {args.out}")


if __name__ == "__main__":
    main()
