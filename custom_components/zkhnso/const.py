"""Constants for the ZKHNSO integration."""

DOMAIN = "zkhnso"

# API URLs
API_BASE_URL = "https://xn--f1aijeow.xn--p1ai/room/lk/"
API_URL_PREFLIGHT = "login.action"
API_URL_LOGIN = "doLogin!enter.action"
API_URL_CHANGE_ACCOUNT_ID = "main!change.action?account=%s"
API_URL_MAIN = "main.action"
API_URL_DETAILS = "accountDetailsPopup.action"
API_URL_TARIFFS = "tariffs.action"
API_URL_METERS = "counters.action"

# Session keys
SESSION_JSESSIONID = "JSESSIONID"
SESSION_FORM_TOKEN = "FORM_TOKEN"

# credentials
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# options flow
CONF_ACCOUNT_OPTIONS = "account_options"
CONF_WASTE_WATER_TARIFF = "waste_water_tariff"
CONF_COLD_WATER_TARIFF = "cold_water_tariff"
CONF_HOT_WATER_TARIFF = "hot_water_tariff"
CONF_HOT_WATER_HEATING_TARIFF = "hot_water_heating_tariff"
CONF_RATE_TYPE = "rate_type"
CONF_HEATING_SYSTEM = "heating_system"
CONF_BUILDING_FEATURES = "building_features"
CONF_MANUAL_RATE = "manual_rate"
CONF_WATER_HEATING_RATE = "water_heating_rate"

# tariffs
WASTE_WATER_TARIFFS = ("ВОДООТВЕДЕНИЕ", "ОТВЕД. СТОЧ. ВОД СОИ")
COLD_WATER_TARIFFS = ("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ.")
HOT_WATER_TARIFFS = ("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", "ГВ НА СОД-Е ОБЩ.ИМУЩ.")
HOT_WATER_HEATING_TARIFFS = ("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)", "ГВ НА СОИ (ПОДОГРЕВ)", "ОТОПЛЕНИЕ")
HOT_WATER_FLUID_TARIFFS = ("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)", "ГВ НА СОИ (ТЕПЛОНОСИТ)", *COLD_WATER_TARIFFS)
