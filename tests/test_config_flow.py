from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skola_online.api.exceptions import CannotConnect, InvalidAuth
from custom_components.skola_online.api.models import Child
from custom_components.skola_online.const import CONF_CHILD_ID, DOMAIN

CREDENTIALS = {CONF_USERNAME: "parent", CONF_PASSWORD: "secret"}


def _client(children: list[Child]) -> AsyncMock:
    client = AsyncMock()
    client.login.return_value = None
    client.list_children.return_value = children
    return client


async def test_single_child_creates_the_entry_without_a_second_step(hass):
    client = _client([Child(id="S001#D1", name="Dítě Jedno")])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dítě Jedno"
    assert result["data"][CONF_CHILD_ID] == "S001#D1"


async def test_several_children_prompt_for_a_choice(hass):
    client = _client(
        [Child(id="S001#D1", name="Dítě Jedno"), Child(id="S001#D2", name="Dítě Druhé")]
    )

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
        assert result["step_id"] == "child"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CHILD_ID: "S001#D2"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dítě Druhé"


@pytest.mark.parametrize(
    ("error", "expected"),
    [(InvalidAuth("no"), "invalid_auth"), (CannotConnect("no"), "cannot_connect")],
)
async def test_login_problems_are_reported_on_the_form(hass, error, expected):
    client = AsyncMock()
    client.login.side_effect = error

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_the_same_child_cannot_be_added_twice(hass):
    MockConfigEntry(
        domain=DOMAIN, unique_id="S001#D1", data={**CREDENTIALS}
    ).add_to_hass(hass)
    client = _client([Child(id="S001#D1", name="Dítě Jedno")])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_stored_password(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="S001#D1",
        data={**CREDENTIALS, CONF_CHILD_ID: "S001#D1"},
    )
    entry.add_to_hass(hass)
    client = _client([Child(id="S001#D1", name="Dítě Jedno")])
    client.fetch_week.return_value = []

    # async_update_reload_and_abort schedules a reload via
    # hass.async_create_task, which runs a real async_setup_entry. Keep
    # SkolaOnlineClient mocked for that reload too, and drain the task
    # before leaving the patch context, so no real network I/O happens.
    with (
        patch(
            "custom_components.skola_online.config_flow.SkolaOnlineClient",
            return_value=client,
        ),
        patch(
            "custom_components.skola_online.SkolaOnlineClient",
            return_value=client,
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "brand-new"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "brand-new"
    # Self-defending: if either patch target above drifts, the reload would
    # construct a real, unmocked SkolaOnlineClient and try to reach the real
    # school portal instead of failing loudly here.
    client.login.assert_awaited()
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_an_account_with_no_children_falls_back_to_the_username(hass):
    """A single-child or teacher account has no dropdown to read.

    list_children() returning [] is not an error — it is the only path such
    accounts ever take, so the unique id and title come from the username and
    no child id is stored.
    """
    client = _client([])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "parent"
    assert result["data"][CONF_CHILD_ID] is None
    assert result["result"].unique_id == "parent"


async def test_a_second_childless_account_of_the_same_name_is_rejected(hass):
    """The username-derived unique id still has to keep duplicates out."""
    MockConfigEntry(domain=DOMAIN, unique_id="parent", data={**CREDENTIALS}).add_to_hass(
        hass
    )
    client = _client([])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_an_unexpected_error_during_reauth_is_reported_on_the_form(hass):
    """Reauth must not crash on a surprise, the way async_step_user doesn't."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="S001#D1",
        data={**CREDENTIALS, CONF_CHILD_ID: "S001#D1"},
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.login.side_effect = RuntimeError("their markup moved again")

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "brand-new"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "unknown"}
    # The stored password is untouched by a failed attempt.
    assert entry.data[CONF_PASSWORD] == "secret"


@pytest.mark.parametrize(
    ("error", "expected"),
    [(InvalidAuth("no"), "invalid_auth"), (CannotConnect("no"), "cannot_connect")],
)
async def test_reauth_login_problems_are_reported_on_the_form(hass, error, expected):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="S001#D1",
        data={**CREDENTIALS, CONF_CHILD_ID: "S001#D1"},
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.login.side_effect = error

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "brand-new"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_the_flow_validates_on_a_session_of_its_own(hass):
    """A validation login must not deposit cookies in the shared jar."""
    sessions = []

    def _make_client(username, password, session, tz):
        sessions.append(session)
        return _client([Child(id="S001#D1", name="Dítě Jedno")])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        side_effect=_make_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)

    assert len(sessions) == 1
    assert sessions[0] is not async_get_clientsession(hass)
    # And the flow does not leave it behind.
    assert sessions[0].closed is True
