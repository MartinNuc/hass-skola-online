"""Config flow: collect credentials, pick a child, and handle reauth."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .api.client import SkolaOnlineClient
from .api.exceptions import CannotConnect, InvalidAuth
from .api.models import Child
from .const import (
    CONF_CHILD_ID,
    CONF_CHILD_NAME,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_WEEKS_AHEAD,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_WEEKS_AHEAD,
    DOMAIN,
    MAX_SCAN_INTERVAL_HOURS,
    MAX_WEEKS_AHEAD,
    MIN_SCAN_INTERVAL_HOURS,
    MIN_WEEKS_AHEAD,
)

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


class SkolaOnlineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Škola Online."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._children: list[Child] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SkolaOnlineOptionsFlow:
        """Create the options flow.

        No __init__/config_entry to wire up: OptionsFlow (the 2026.2 base
        class) already exposes self.config_entry as a property once the flow
        manager attaches hass/handler, and assigning to it ourselves would
        clash with that property (it has no setter).
        """
        return SkolaOnlineOptionsFlow()

    async def _authenticate(self, credentials: Mapping[str, str]) -> list[Child]:
        """Log in for real, then read the children the account can see.

        On its own session, closed again on the way out: a validation login
        must not deposit its cookies in a jar shared with loaded entries, and
        a flow the user abandons must not leave a session behind.
        """
        session = async_create_clientsession(self.hass)
        try:
            client = SkolaOnlineClient(
                credentials[CONF_USERNAME],
                credentials[CONF_PASSWORD],
                session,
                dt_util.get_default_time_zone(),
            )
            await client.login()
            return await client.list_children()
        finally:
            # Detach rather than close: the connector belongs to Home
            # Assistant and is shared with every other session.
            session.detach()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                children = await self._authenticate(user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 — surface, never crash the flow
                _LOGGER.exception("unexpected error during Škola Online login")
                errors["base"] = "unknown"
            else:
                self._credentials = dict(user_input)
                self._children = children

                if len(children) > 1:
                    return await self.async_step_child()

                child = children[0] if children else None
                return await self._create_entry(child)

        return self.async_show_form(
            step_id="user", data_schema=CREDENTIALS_SCHEMA, errors=errors
        )

    async def async_step_child(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which child this entry should follow."""
        if user_input is not None:
            chosen = next(
                c for c in self._children if c.id == user_input[CONF_CHILD_ID]
            )
            return await self._create_entry(chosen)

        return self.async_show_form(
            step_id="child",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CHILD_ID): vol.In(
                        {child.id: child.name for child in self._children}
                    )
                }
            ),
        )

    async def _create_entry(self, child: Child | None) -> ConfigFlowResult:
        """One entry per child, so siblings stay independently reloadable."""
        unique_id = child.id if child else self._credentials[CONF_USERNAME]
        title = child.name if child else self._credentials[CONF_USERNAME]

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=title,
            data={
                **self._credentials,
                CONF_CHILD_ID: child.id if child else None,
                CONF_CHILD_NAME: title,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._credentials = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a new password rather than going quietly stale."""
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = {**self._credentials, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await self._authenticate(candidate)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 — surface, never crash the flow
                _LOGGER.exception("unexpected error during Škola Online login")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


class SkolaOnlineOptionsFlow(OptionsFlowWithReload):
    """Let the refresh interval and fetch window be tuned from the UI.

    Subclassing OptionsFlowWithReload means Home Assistant reloads the entry
    for us as soon as new options are saved, with no update listener to
    write or keep in sync — the coordinator just picks the new values up on
    the reload's fresh __init__. (OptionsFlowManager actively forbids mixing
    the two: registering an update listener on an entry whose options flow
    uses this base class raises ValueError.)
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The only step: interval and fetch window, one form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_HOURS,
                    default=options.get(
                        CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
                    ),
                ): vol.All(
                    # A school timetable does not need minute-level polling,
                    # and a lower bound of one hour (rather than allowing a
                    # much shorter interval) keeps this a polite guest on
                    # skolaonline.cz's server rather than hammering it.
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_HOURS,
                            max=MAX_SCAN_INTERVAL_HOURS,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="h",
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Required(
                    CONF_WEEKS_AHEAD,
                    default=options.get(CONF_WEEKS_AHEAD, DEFAULT_WEEKS_AHEAD),
                ): vol.All(
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_WEEKS_AHEAD,
                            max=MAX_WEEKS_AHEAD,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Coerce(int),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
