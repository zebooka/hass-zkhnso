"""Config flow for ZKHNSO integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api_client import ZKHAPIClient, ZKHAPIConnectionError
from .const import (
    CONF_ACCOUNT_ID,
    CONF_ADDRESS,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)

STEP_RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password.strip():
        raise InvalidAuth

    async with ZKHAPIClient(username, password) as client:
        if not await client.preflight():
            raise CannotConnect
        try:
            logged_in = await client.login()
        except ZKHAPIConnectionError as err:
            raise CannotConnect from err
        if not logged_in:
            raise InvalidAuth

    if client.account_id is None or not client.address:
        raise AccountParseError

    return {
        "title": f"№{client.account_id} {client.address}",
        "username": username,
        "account_id": client.account_id,
        "address": client.address,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZKH."""

    VERSION = 1

    async def _async_step_credentials(
        self, user_input: dict[str, Any] | None, *, reauth: bool = False
    ) -> FlowResult:
        """Validate credentials and create or update a config entry."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm" if reauth else "user",
                data_schema=STEP_USER_DATA_SCHEMA,
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except AccountParseError:
            errors["base"] = "account_parse_error"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            entry_data = {
                CONF_USERNAME: info["username"],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_ACCOUNT_ID: info["account_id"],
                CONF_ADDRESS: info["address"],
            }
            if reauth:
                self.hass.config_entries.async_update_entry(
                    self._get_reauth_entry(),
                    data=entry_data,
                    title=info["title"],
                )
                await self.hass.config_entries.async_reload(
                    self._get_reauth_entry().entry_id
                )
                return self.async_abort(reason="reauth_successful")

            await self.async_set_unique_id(info["username"].casefold())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="reauth_confirm" if reauth else "user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle initial configuration."""
        return await self._async_step_credentials(user_input)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle reauthentication initiated by Home Assistant."""
        self.context["title_placeholders"] = {
            "name": self._get_reauth_entry().title,
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm updated credentials."""
        return await self._async_step_credentials(user_input, reauth=True)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Update credentials for the same account without replacing the entry."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except AccountParseError:
                errors["base"] = "account_parse_error"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reconfiguration")
                errors["base"] = "unknown"
            else:
                old_account_id = entry.data.get(CONF_ACCOUNT_ID)
                if old_account_id is None:
                    errors["base"] = "account_id_missing"
                elif str(old_account_id) != str(info["account_id"]):
                    errors["base"] = "account_mismatch"
                else:
                    username = info["username"].casefold()
                    other_entries = self.hass.config_entries.async_entries(DOMAIN)
                    if any(
                        other.entry_id != entry.entry_id
                        and (
                            other.unique_id == username
                            or str(other.data.get(CONF_USERNAME, "")).casefold() == username
                        )
                        for other in other_entries
                    ):
                        errors["base"] = "already_configured"
                    else:
                        return self.async_update_reload_and_abort(
                            entry,
                            unique_id=username,
                            title=info["title"],
                            data_updates={
                                CONF_USERNAME: info["username"],
                                CONF_PASSWORD: user_input[CONF_PASSWORD],
                                CONF_ACCOUNT_ID: info["account_id"],
                                CONF_ADDRESS: info["address"],
                            },
                        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_RECONFIGURE_SCHEMA,
                {CONF_USERNAME: entry.data.get(CONF_USERNAME, "")},
            ),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class AccountParseError(HomeAssistantError):
    """Error to indicate account details could not be parsed after login."""
