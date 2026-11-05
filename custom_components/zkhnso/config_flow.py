"""Config flow for ZKHNSO integration."""
from __future__ import annotations

import logging
import math
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectSelector, SelectSelectorConfig, SelectSelectorMode,
    TextSelector, TextSelectorConfig, TextSelectorType,
)

from .api_client import ZKHAPIClient, ZKHAPIConnectionError
from .const import (
    CONF_ACCOUNT_ID,
    CONF_ADDRESS,
    CONF_BUILDING_FEATURES,
    CONF_HEATING_SYSTEM,
    CONF_MANUAL_RATE,
    CONF_PASSWORD,
    CONF_RATE_TYPE,
    CONF_USERNAME,
    CONF_WATER_HEATING_RATE,
    DOMAIN,
)
from .water_heating import BUILDING_FEATURES, RATE_TYPES, SYSTEMS, water_heating_rate

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


def _select(options: tuple[str, ...], translation_key: str) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(
        options=list(options), mode=SelectSelectorMode.DROPDOWN,
        translation_key=translation_key,
    ))


class WaterHeatingFlow:
    """Shared multi-step water heating configuration for setup and options."""

    def _heating_options(self) -> dict[str, Any]:
        if not hasattr(self, "_rate_options"):
            self._rate_options = dict(self._existing_heating_options())
        return self._rate_options

    def _existing_heating_options(self) -> dict[str, Any]:
        return {}

    def _rate_form(self, step: str, schema: vol.Schema, errors=None, placeholders=None):
        return self.async_show_form(
            step_id=step,
            data_schema=self.add_suggested_values_to_schema(schema, self._heating_options()),
            errors=errors or {},
            description_placeholders=placeholders,
        )

    async def async_step_rate_type(self, user_input=None):
        """Choose whether a water heating rate is configured."""
        options = self._heating_options()
        errors = {}
        if user_input is not None:
            rate_type = user_input.get(CONF_RATE_TYPE)
            if rate_type not in RATE_TYPES:
                errors["base"] = "invalid_rate_type"
            else:
                options[CONF_RATE_TYPE] = rate_type
                if rate_type == "none":
                    options[CONF_WATER_HEATING_RATE] = None
                    return await self._finish_water_heating()
                if rate_type == "manual":
                    return await self.async_step_rate_manual()
                return await self.async_step_rate_auto()
        options.setdefault(CONF_RATE_TYPE, "none")
        return self._rate_type_form(vol.Schema({
            vol.Required(CONF_RATE_TYPE): _select(RATE_TYPES, "rate_type")
        }), errors)

    def _rate_type_form(self, schema: vol.Schema, errors: dict[str, str]):
        """Show the rate type form during initial setup."""
        return self._rate_form("rate_type", schema, errors)

    async def async_step_rate_manual(self, user_input=None):
        """Enter a finite, non-negative rate."""
        options = self._heating_options()
        errors = {}
        if user_input is not None:
            try:
                value = float(str(user_input[CONF_MANUAL_RATE]).strip().replace(",", "."))
                if not math.isfinite(value) or value < 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_rate"
                options[CONF_MANUAL_RATE] = user_input.get(CONF_MANUAL_RATE, "")
            else:
                options[CONF_MANUAL_RATE] = str(user_input[CONF_MANUAL_RATE])
                options[CONF_WATER_HEATING_RATE] = value
                return await self._finish_water_heating()
        return self._rate_form("rate_manual", vol.Schema({
            vol.Required(CONF_MANUAL_RATE): str
        }), errors)

    async def async_step_rate_auto(self, user_input=None):
        """Select the system and building features used to calculate the rate."""
        options = self._heating_options()
        errors = {}
        if user_input is not None:
            try:
                rate = water_heating_rate(
                    user_input[CONF_HEATING_SYSTEM], user_input[CONF_BUILDING_FEATURES]
                )
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_selection"
            else:
                options.update({
                    CONF_HEATING_SYSTEM: user_input[CONF_HEATING_SYSTEM],
                    CONF_BUILDING_FEATURES: user_input[CONF_BUILDING_FEATURES],
                    CONF_WATER_HEATING_RATE: rate,
                })
                return await self.async_step_rate_confirm()
        return self._rate_form("rate_auto", vol.Schema({
            vol.Required(CONF_BUILDING_FEATURES): _select(BUILDING_FEATURES, "building_features"),
            vol.Required(CONF_HEATING_SYSTEM): _select(SYSTEMS, "heating_system"),
        }), errors)

    async def async_step_rate_confirm(self, user_input=None):
        """Show and confirm the calculated rate."""
        options = self._heating_options()
        if user_input is not None:
            return await self._finish_water_heating()
        return self._rate_form(
            "rate_confirm", vol.Schema({}),
            placeholders={"rate": f"{options[CONF_WATER_HEATING_RATE]:.5f}"},
        )


class ConfigFlow(WaterHeatingFlow, config_entries.ConfigFlow, domain=DOMAIN):
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
            self._entry_data = entry_data
            self._entry_title = info["title"]
            return await self.async_step_rate_type()

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

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Provide an options flow for an existing integration entry."""
        return ZKHOptionsFlow()

    async def _finish_water_heating(self):
        """Create the entry only after the heating rate has been selected."""
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._entry_title, data=self._entry_data, options=self._heating_options()
        )

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


class ZKHOptionsFlow(WaterHeatingFlow, config_entries.OptionsFlow):
    """Manage water heating options for an existing entry."""

    def _existing_heating_options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    def _rate_type_form(self, schema: vol.Schema, errors: dict[str, str]):
        """Display the saved configuration below the type selector."""
        options = self.config_entry.options
        rate = options.get(CONF_WATER_HEATING_RATE)
        is_auto = options.get(CONF_RATE_TYPE) == "auto"
        features = options.get(CONF_BUILDING_FEATURES)
        system = options.get(CONF_HEATING_SYSTEM)
        placeholders = {
            "building_features": features if is_auto and features in BUILDING_FEATURES else "not_set",
            "heating_system": system if is_auto and system in SYSTEMS else "not_set",
        }
        if rate is None:
            return self._rate_form("rate_type_unset", schema, errors, placeholders)
        placeholders["rate"] = str(rate)
        return self._rate_form(
            "rate_type", schema, errors, placeholders=placeholders
        )

    async def async_step_init(self, user_input=None):
        return await self.async_step_rate_type(user_input)

    async def async_step_rate_type_unset(self, user_input=None):
        """Handle the rate type form when no rate has been saved."""
        return await self.async_step_rate_type(user_input)

    async def _finish_water_heating(self):
        return self.async_create_entry(data=self._heating_options())


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class AccountParseError(HomeAssistantError):
    """Error to indicate account details could not be parsed after login."""
