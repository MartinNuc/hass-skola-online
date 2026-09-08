"""Errors raised by the Škola Online scraper.

These are deliberately independent of Home Assistant's exception types; the
HA layer translates them at its own boundary.
"""


class SkolaOnlineError(Exception):
    """Base class for every error this package raises."""


class CannotConnect(SkolaOnlineError):
    """The school's server could not be reached, or returned a server error."""


class InvalidAuth(SkolaOnlineError):
    """The username or password was rejected."""


class SessionExpired(SkolaOnlineError):
    """The session cookie is no longer valid and a fresh login is required."""


class ParseError(SkolaOnlineError):
    """The page loaded but did not look like the page we expected."""
