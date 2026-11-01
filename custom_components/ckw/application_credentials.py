"""OAuth2 application credentials for the CKW Smart Meter integration.

CKW registers the integration as a public Keycloak client: no client secret is
issued, so the OAuth2 implementation is a PKCE client and Home Assistant's own
OAuth2 session performs the code exchange and the refresh (ADR-0001, ADR-0006).

The production endpoints are the defaults in ``const.py``. The local dev
instance may set ``CKW_AUTHORIZE_URL`` and ``CKW_TOKEN_URL`` (the values in
``scripts/dev/.env.example``) to point at the mock IdP instead; production
installations never set them.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from homeassistant.components.application_credentials import (
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2Implementation,
    LocalOAuth2ImplementationWithPkce,
)

from .const import (
    AUTHORIZE_URL,
    ENV_AUTHORIZE_URL,
    ENV_TOKEN_URL,
    SCOPES,
    TOKEN_URL,
)

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: Endpoint pairs already reported, so a debug log names the active pair once
#: per process instead of on every config-flow start.
_LOGGED_ENDPOINT_PAIRS: Final[set[tuple[str, str]]] = set()


def _authorization_server_urls() -> tuple[str, str]:
    """Return the authorize and token URL, honouring the dev overrides."""
    authorize_url = os.environ.get(ENV_AUTHORIZE_URL, AUTHORIZE_URL)
    token_url = os.environ.get(ENV_TOKEN_URL, TOKEN_URL)
    pair = (authorize_url, token_url)
    if pair not in _LOGGED_ENDPOINT_PAIRS:
        _LOGGED_ENDPOINT_PAIRS.add(pair)
        overridden = any(os.environ.get(name) for name in (ENV_AUTHORIZE_URL, ENV_TOKEN_URL))
        source = "development override" if overridden else "production"
        _LOGGER.debug("using %s OAuth endpoints: %s, %s", source, *pair)
    return pair


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return the CKW Single Sign-On authorization server."""
    authorize_url, token_url = _authorization_server_urls()
    return AuthorizationServer(authorize_url=authorize_url, token_url=token_url)


async def async_get_auth_implementation(
    hass: HomeAssistant,
    auth_domain: str,
    credential: ClientCredential,
) -> AbstractOAuth2Implementation:
    """Return the PKCE implementation for the registered CKW client."""
    authorize_url, token_url = _authorization_server_urls()
    return CkwOAuth2Implementation(
        hass,
        auth_domain,
        credential.client_id,
        authorize_url,
        token_url,
    )


class CkwOAuth2Implementation(LocalOAuth2ImplementationWithPkce):
    """Public OAuth2 client with PKCE and CKW's scopes."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth_domain: str,
        client_id: str,
        authorize_url: str,
        token_url: str,
    ) -> None:
        """Initialize the public client; an application credential's secret is ignored."""
        super().__init__(
            hass,
            auth_domain,
            client_id,
            authorize_url,
            token_url,
            client_secret="",
        )

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Add the scopes to the PKCE authorize request."""
        return super().extra_authorize_data | {"scope": " ".join(SCOPES)}
