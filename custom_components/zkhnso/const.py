"""Constants for the ZKHNSO integration."""

DOMAIN = "zkhnso"

# API URLs
API_BASE_URL = "https://xn--f1aijeow.xn--p1ai/room/lk/"
API_URL_PREFLIGHT = "login.action"
API_URL_LOGIN = "doLogin!enter.action"
API_URL_TARIFFS = "tariffs.action"
API_URL_METERS = "counters.action"
API_URL_MAIN = "main.action"
API_URL_CHANGE_ACCOUNT_ID = "main!change.action?account=%s"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCOUNT_ID = "account_id"
CONF_ADDRESS = "address"

CONF_WATER_HEATING_RATE = "water_heating_rate"
CONF_RATE_TYPE = "rate_type"
CONF_HEATING_SYSTEM = "heating_system"
CONF_BUILDING_FEATURES = "building_features"
CONF_MANUAL_RATE = "manual_rate"

# Session keys
SESSION_JSESSIONID = "JSESSIONID"
SESSION_FORM_TOKEN = "FORM_TOKEN"
