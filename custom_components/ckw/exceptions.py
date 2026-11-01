"""Translated errors a CKW user can see (Gold rule ``exception-translations``).

``api.py`` owns HTTP and raises its own typed exceptions; ``coordinator.py``
turns them into ``UpdateFailed``/``ConfigEntryAuthFailed`` for a poll. This
module is the third surface: the errors a service raises straight back to the
user. Every class carries ``translation_domain`` and a semantic
``translation_key`` that exists in ``strings.json`` (``exceptions:``), so the
frontend renders the sentence in the user's language and no English literal or
stack trace from code reaches it (``docs/reference/conventions.md``, "Error
mapping", #130).
"""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

from .api import (
    CkwApiError,
    CkwAuthError,
    CkwConnectionError,
    CkwError,
    CkwRateLimitError,
)
from .api import CkwRangeTooBigError as CkwRangeCapError
from .const import DOMAIN

__all__ = [
    "CkwApiStatusError",
    "CkwAuthenticationError",
    "CkwConnectionFailedError",
    "CkwException",
    "CkwNoDataError",
    "CkwNoMetersError",
    "CkwRangeTooBigError",
    "CkwRateLimitedError",
    "CkwUnknownError",
    "error_for_api_failure",
    "format_reset_seconds",
]


class CkwException(HomeAssistantError):
    """Base class of every translated CKW error.

    Only the condition is passed in; the sentence lives in ``strings.json``.
    A translation key names the condition, never the HTTP status, so the same
    key keeps working when CKW changes which status it answers with.
    """

    def __init__(
        self,
        *,
        translation_key: str,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        """Build the error from its translation key and placeholders."""
        super().__init__(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders=translation_placeholders,
        )


class CkwAuthenticationError(CkwException):
    """CKW rejected the sign-in; only a new login repairs it (ADR-0007)."""

    def __init__(self) -> None:
        """Name the invalid-auth condition."""
        super().__init__(translation_key="invalid_auth")


class CkwRateLimitedError(CkwException):
    """``429`` - the quota is exhausted, waiting is the only fix.

    The reset time is rounded to whole seconds for the message; without a
    reported reset the wording must not promise a time it does not know.
    """

    def __init__(self, reset_seconds: float | None = None) -> None:
        """Name the rate limit and, when known, when the window resets."""
        self.reset_seconds: float | None = reset_seconds
        if reset_seconds is None or reset_seconds <= 0:
            super().__init__(translation_key="rate_limited_no_reset")
            return
        super().__init__(
            translation_key="rate_limited",
            translation_placeholders={"reset": format_reset_seconds(reset_seconds)},
        )


class CkwRangeTooBigError(CkwException):
    """``400`` "response too big": the requested window exceeds the CKW cap."""

    def __init__(self) -> None:
        """Name the range-cap condition."""
        super().__init__(translation_key="range_too_big")


class CkwApiStatusError(CkwException):
    """Any other status CKW answered with; the number is a placeholder."""

    def __init__(self, status: int) -> None:
        """Name the unexpected answer and carry its status."""
        super().__init__(
            translation_key="api_error",
            translation_placeholders={"status": str(status)},
        )
        self.status = status


class CkwNoMetersError(CkwException):
    """The account has no electricity smart meter this integration can read."""

    def __init__(self) -> None:
        """Name the no-meters condition."""
        super().__init__(translation_key="no_meters")


class CkwNoDataError(CkwException):
    """CKW answered, but nothing in the window was importable."""

    def __init__(self) -> None:
        """Name the no-data condition."""
        super().__init__(translation_key="no_data")


class CkwConnectionFailedError(CkwException):
    """The request never produced a response: offline, timeout or DNS."""

    def __init__(self) -> None:
        """Name the connection condition."""
        super().__init__(translation_key="cannot_connect")


class CkwUnknownError(CkwException):
    """A failure this integration has no more specific key for."""

    def __init__(self) -> None:
        """Name the generic condition."""
        super().__init__(translation_key="unknown")


def format_reset_seconds(seconds: float) -> str:
    """Render a rate-limit wait for the ``{reset}`` placeholder."""
    return f"{max(seconds, 0.0):.0f} s"


def error_for_api_failure(err: CkwError) -> CkwException:
    """Translate one ``api.py`` failure into the error a user sees.

    The mapping lives here and nowhere else, so a service never has to know
    which HTTP status means which translation key. ``CkwRangeTooBigError`` is an
    ``CkwApiError`` and is therefore tested first.
    """
    if isinstance(err, CkwAuthError):
        return CkwAuthenticationError()
    if isinstance(err, CkwRateLimitError):
        return CkwRateLimitedError(err.retry_after_seconds)
    if isinstance(err, CkwRangeCapError):
        return CkwRangeTooBigError()
    if isinstance(err, CkwApiError):
        return CkwApiStatusError(err.status)
    if isinstance(err, CkwConnectionError):
        return CkwConnectionFailedError()
    return CkwUnknownError()
