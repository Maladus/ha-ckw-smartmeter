"""Tests for the domain's actions and when they exist (Bronze `action-setup`).

The integration has exactly one action, ``ckw.backfill``. The quality-scale rule
asks for it to be registered when the integration loads, not per config entry,
so that it exists before any account is set up and so that the handler validates
the entry it was given instead of capturing one. These tests pin that: the
action appears from ``async_setup`` alone, a second registration is a no-op, and
an impossible target fails as a translated ``HomeAssistantError``.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components.ckw import async_setup
from custom_components.ckw.const import DOMAIN, SERVICE_BACKFILL

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def registered_handlers(hass: HomeAssistant) -> dict[str, object]:
    """Return the handlers registered for the CKW domain."""
    return dict(hass.services.async_services().get(DOMAIN, {}))


async def test_the_action_exists_as_soon_as_the_domain_loads(hass: HomeAssistant) -> None:
    """Loading the integration registers the action, with no config entry at all."""
    assert await async_setup_component(hass, DOMAIN, {})

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert hass.services.has_service(DOMAIN, SERVICE_BACKFILL)
    assert list(registered_handlers(hass)) == [SERVICE_BACKFILL]


async def test_loading_the_action_twice_keeps_one_handler(hass: HomeAssistant) -> None:
    """A reload, or a second setup call, must not replace the registered handler."""
    assert await async_setup(hass, {})
    first = registered_handlers(hass)[SERVICE_BACKFILL]

    assert await async_setup(hass, {})

    assert list(registered_handlers(hass)) == [SERVICE_BACKFILL]
    assert registered_handlers(hass)[SERVICE_BACKFILL] is first


@pytest.mark.parametrize(
    ("data", "key", "placeholder"),
    [
        ({}, "no_loaded_entry", None),
        ({"entry_id": "01J9Z5S8XQ7QK3M4N6P8R0T2V4"}, "unknown_entry", "01J9Z5S8XQ7QK3M4N6P8R0T2V4"),
    ],
)
async def test_an_impossible_target_raises_a_translated_error(
    hass: HomeAssistant,
    data: dict[str, str],
    key: str,
    placeholder: str | None,
) -> None:
    """Without a matching loaded entry the action names the problem, not a traceback."""
    assert await async_setup(hass, {})

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(DOMAIN, SERVICE_BACKFILL, data, blocking=True)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == key
    if placeholder is not None:
        assert raised.value.translation_placeholders == {"entry_id": placeholder}
