"""Sanity tests for the constants module.

These do not touch Home Assistant beyond importing ``homeassistant.const.Platform``
through ``const.py``; the integration's behaviour is tested where it is implemented.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

from custom_components.ckw import const

DOCUMENTED_AGGREGATIONS: Final[frozenset[str]] = frozenset(
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


def test_aggregations_are_unique_and_complete() -> None:
    """The API accepts ten aggregation values, each spelled once."""
    assert len(const.AGGREGATIONS) == len(set(const.AGGREGATIONS))
    assert set(const.AGGREGATIONS) == DOCUMENTED_AGGREGATIONS


def test_mock_base_url_shape() -> None:
    """The mock base URL points at the local mock's API root."""
    parts = urlsplit(const.MOCK_BASE_URL)
    assert parts.scheme == "http"
    assert parts.netloc == "localhost:8787"
    assert parts.path == "/api/v1"
    assert parts.query == ""
    assert not const.MOCK_BASE_URL.endswith("/")


def test_poll_bounds_are_ordered() -> None:
    """The default interval sits strictly inside the allowed range."""
    assert const.MIN_POLL_HOURS < const.DEFAULT_POLL_HOURS < const.MAX_POLL_HOURS


def test_config_keys_are_strings() -> None:
    """Every config key is a plain string, never an enum or a number."""
    config_keys = {name: value for name, value in vars(const).items() if name.startswith("CONF_")}
    assert config_keys
    for name, value in config_keys.items():
        assert isinstance(value, str), f"{name} is not a str"
