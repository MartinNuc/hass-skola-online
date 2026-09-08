"""Constants for the Škola Online integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "skola_online"

BASE_URL: Final = "https://aplikace.skolaonline.cz"
LOGIN_URL: Final = f"{BASE_URL}/SOL/Prihlaseni.aspx"
CALENDAR_URL: Final = f"{BASE_URL}/SOL/App/Kalendar/KZK001_KalendarTyden.aspx"

# The school's server stores the selected week in session state, so the
# coordinator always posts an explicit date. Six hours is a compromise between
# noticing a substitution and being a polite guest on their server.
#
# Kept for backwards compatibility only: the coordinator now reads the
# interval from the config entry's options (see CONF_SCAN_INTERVAL_HOURS
# below), falling back to DEFAULT_SCAN_INTERVAL_HOURS, not this constant.
SCAN_INTERVAL: Final = timedelta(hours=6)

# Current week plus the next three.
#
# Kept for backwards compatibility only: the coordinator now reads the week
# count from the config entry's options (see CONF_WEEKS_AHEAD below), falling
# back to DEFAULT_WEEKS_AHEAD, not this constant.
WEEKS_AHEAD: Final = 4

CONF_CHILD_ID: Final = "child_id"
CONF_CHILD_NAME: Final = "child_name"

# Options-flow keys: the refresh interval and fetch window, editable from
# Settings -> Devices & Services -> Configure instead of fixed in code.
CONF_SCAN_INTERVAL_HOURS: Final = "scan_interval_hours"
CONF_WEEKS_AHEAD: Final = "weeks_ahead"

DEFAULT_SCAN_INTERVAL_HOURS: Final = 6
DEFAULT_WEEKS_AHEAD: Final = 4

# Bounds enforced by the options flow. Expressed in whole hours, not minutes:
# this is a school timetable, not a stock ticker, and a lower bound of one
# hour (rather than allowing e.g. every few minutes) keeps the integration a
# polite guest on skolaonline.cz's server instead of hammering it.
MIN_SCAN_INTERVAL_HOURS: Final = 1
MAX_SCAN_INTERVAL_HOURS: Final = 24

# Current week plus at most seven more; asking for less than one week doesn't
# make sense for a "weeks ahead" setting.
MIN_WEEKS_AHEAD: Final = 1
MAX_WEEKS_AHEAD: Final = 8
