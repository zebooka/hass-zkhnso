"""API client for ZKHNSO integration."""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

import aiohttp

from .const import (
    API_BASE_URL,
    API_URL_CHANGE_ACCOUNT_ID,
    API_URL_LOGIN,
    API_URL_MAIN,
    API_URL_METERS,
    API_URL_TARIFFS,
    API_URL_PREFLIGHT,
    SESSION_FORM_TOKEN,
    SESSION_JSESSIONID,
)
from .html_parser import extract_cells_from_table_rows, extract_cell_text, extract_cell_href, html_to_str, html_to_json

_LOGGER = logging.getLogger(__name__)


class ZKHAPIConnectionError(Exception):
    """The ZKHNSO service could not be reached or returned an error."""


class ZKHAPIClient:
    """Client for interacting with ZKHNSO API."""

    def __init__(self, username: str, password: str) -> None:
        """Initialize the API client with an isolated HTTP session."""
        self.username = username
        self.password = password
        self.session: aiohttp.ClientSession | None = None
        self.jsessionid: str | None = None
        self.form_token: str | None = None
        self.current_account_id: str | None = None
        self.accounts: dict[str, str] = {}

    async def __aenter__(self) -> ZKHAPIClient:
        """Create an HTTP session and cookie jar isolated to this client."""
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close the HTTP session and discard its account-specific cookies."""
        if self.session is not None:
            await self.session.close()
            self.session = None

    @property
    def _session(self) -> aiohttp.ClientSession:
        """Return the active session, requiring use as an async context manager."""
        if self.session is None:
            raise RuntimeError("ZKHAPIClient must be used as an async context manager")
        return self.session

    async def preflight(self) -> dict[str, str] | None:
        """Perform preflight request to get FORM_TOKEN and JSESSIONID."""
        url = urljoin(API_BASE_URL, API_URL_PREFLIGHT)

        try:
            async with self._session.get(url) as response:
                if response.status != 200:
                    _LOGGER.error("Preflight request to url %s failed with status %d", url, response.status)
                    return None

                jsessionid = self._extract_jsessionid(response.headers)
                if not jsessionid:
                    _LOGGER.warning("JSESSIONID not found in preflight response")
                    return None
                self.jsessionid = jsessionid

                html = await response.text()
                form_token = html_to_str(html, "#loginForm input[name=loginToken]", attribute="value")
                if not form_token:
                    _LOGGER.error("FORM_TOKEN not found in HTML response")
                    return None
                self.form_token = form_token

                _LOGGER.debug("Preflight successful, session initialized")

                return {
                    SESSION_JSESSIONID: jsessionid,
                    SESSION_FORM_TOKEN: form_token,
                }

        except aiohttp.ClientError as e:
            _LOGGER.error("Error during preflight request: %s", e)
            return None
        except Exception as e:
            _LOGGER.exception("Unexpected error during preflight: %s", e)
            return None

    def _extract_jsessionid(self, headers: Any) -> str | None:
        """Extract JSESSIONID from Set-Cookie header."""
        cookies = headers.getall("Set-Cookie", [])
        for cookie in cookies:
            if "JSESSIONID=" in cookie:
                parts = cookie.split(";")
                for part in parts:
                    part = part.strip()
                    if part.startswith("JSESSIONID="):
                        return part.split("=", 1)[1]
        return None

    def _get_authenticated_cookies(self) -> dict[str, str]:
        """Return application cookies for authenticated requests.

        Server-issued session cookies, including JSESSIONID, are managed by the
        isolated aiohttp CookieJar and must not be replaced manually.
        """
        return {
            "userLogin": self.username,
            "loginModule": "lk",
        }

    @staticmethod
    def _is_login_page(html: str) -> bool:
        """Return whether an HTML response contains the login form."""
        return (
                'id="loginForm"' in html
                or "id='loginForm'" in html
                or 'name="loginToken"' in html
                or "name='loginToken'" in html
        )

    @staticmethod
    def _log_page_diagnostics(page_name: str, response: aiohttp.ClientResponse, html_content: str) -> None:
        _LOGGER.debug("%s response: status=%d, url=%s", page_name, response.status, response.url)

    async def login(self) -> bool:
        """Log in, returning False only when the login form is returned."""
        if not self.form_token or not self.jsessionid:
            raise ZKHAPIConnectionError("Preflight must be called before login")

        url = urljoin(API_BASE_URL, API_URL_LOGIN)
        form_payload = urlencode(
            {
                "struts.token.name": "loginToken",
                "loginToken": self.form_token,
                "userName": self.username,
                "userPass": self.password,
                "captchaCode": "x",
                "timezone": "-420",
                "loginModule": "lk",
            },
            doseq=False,
            encoding="utf-8",
            safe="",
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": API_BASE_URL,
            "Referer": urljoin(API_BASE_URL, API_URL_PREFLIGHT),
        }

        _LOGGER.debug("Login: %s", self.username)

        try:
            async with self._session.post(
                    url,
                    data=form_payload,
                    headers=headers,
            ) as response:
                if response.status != 200:
                    raise ZKHAPIConnectionError(
                        f"Login request failed with HTTP status {response.status}"
                    )
                html = await response.text()
                self._log_page_diagnostics("Login", response, html)

                updated_jsessionid = self._extract_jsessionid(response.headers)
                if updated_jsessionid:
                    self.jsessionid = updated_jsessionid
                    _LOGGER.debug("JSESSIONID updated after login")

                if not self._is_login_page(html):

                    self.current_account_id = html_to_str(html, ".header .account span")
                    if self.current_account_id is None:
                        raise ZKHAPIConnectionError("Login page has no current account information")

                    self.accounts = {self.current_account_id: None}

                    rows = extract_cells_from_table_rows(html, ".header .account .acc-select .accounts-grid tr") or []
                    for row in rows:
                        if len(row) < 2:
                            continue
                        account_id = extract_cell_text(row[0]).removeprefix("ЛС:").strip() or None
                        change_url = extract_cell_href(row[0])
                        if account_id is not None:
                            self.accounts[account_id] = change_url

                    _LOGGER.debug("Login response accepted")
                    return True

                _LOGGER.warning(
                    "Login was not accepted: status=%d, login_form=%s",
                    response.status,
                    self._is_login_page(html),
                )
                return False

        except ZKHAPIConnectionError:
            raise
        except Exception as err:
            raise ZKHAPIConnectionError("Failed to connect to ZKHNSO during login") from err

    async def change_account(self, account_id: str) -> None:
        """Switch the session to another account, verifying the expected redirect."""
        if not self.jsessionid:
            raise ZKHAPIConnectionError("Must be logged in to change account")
        if not self.accounts[account_id]:
            raise ZKHAPIConnectionError("Account has no change url")
        url = urljoin(API_BASE_URL, self.accounts[account_id])
        try:
            async with self._session.get(
                url, allow_redirects=False,
                headers={"Referer": urljoin(API_BASE_URL, API_URL_MAIN)},
                cookies=self._get_authenticated_cookies(),
            ) as response:
                if response.status >= 400:
                    raise ZKHAPIConnectionError(f"Account switch returned an unexpected response {response.status}")
                self.current_account_id = account_id
        except ZKHAPIConnectionError:
            raise
        except Exception as err:
            raise ZKHAPIConnectionError("Failed to switch ZKHNSO account") from err

    async def _get_page(self, url: str, page_name: str) -> str | None:
        """Fetch an authenticated page."""
        if not self.jsessionid:
            _LOGGER.error("Must be logged in to fetch %s", page_name.casefold())
            return None

        try:
            async with self._session.get(
                url,
                headers={"Referer": urljoin(API_BASE_URL, API_URL_MAIN)},
                cookies=self._get_authenticated_cookies(),
            ) as response:
                if response.status != 200:
                    _LOGGER.error(
                        "%s request to url %s failed with status %d",
                        page_name,
                        url,
                        response.status,
                    )
                    return None

                html_content = await response.text()
                self._log_page_diagnostics(page_name, response, html_content)
                if self._is_login_page(html_content):
                    _LOGGER.warning("%s response returned the login page", page_name)
                    return None

                return html_content
        except aiohttp.ClientError as err:
            _LOGGER.error("Error during %s request: %s", page_name.casefold(), err)
            return None
        except Exception as err:
            _LOGGER.exception("Unexpected error during %s fetch: %s", page_name.casefold(), err)
            return None

    async def get_address_info(self) -> dict[str, Any] | None:
        url = urljoin(API_BASE_URL, API_URL_MAIN)
        html = await self._get_page(url, "Address info")
        if not html:
            return None

        return {
            "address": html_to_str(html, ".header .address"),
            "company_name": html_to_str(html, ".main-content .contacts h2"),
            "company_address": html_to_str(html, ".main-content .contacts div.text"),
        }

    async def _get_data(self, url: str, selector: str, page_name: str) -> list[list[str]] | None:
        """Fetch an authenticated page and extract rows from its table."""
        html = await self._get_page(url, page_name)
        if not html:
            return None

        rows = extract_cells_from_table_rows(html, selector)
        if not rows:
            _LOGGER.warning("No %s table rows found at %s", page_name.casefold(), url)
            return []

        return [[extract_cell_text(cell) for cell in row] for row in rows]

    async def get_meters(self) -> dict[str, Any] | None:
        """Fetch meters/counters data."""
        url = urljoin(API_BASE_URL, API_URL_METERS)
        rows = await self._get_data(url, "#countersForm table tr", "Meters")
        return self._process_meters_data(rows) if rows is not None else None

    async def get_tariffs(self) -> dict[str, Any] | None:
        """Fetch tariffs data."""
        url = urljoin(API_BASE_URL, API_URL_TARIFFS)
        rows = await self._get_data(url, "#tariffsForm table tr", "Tariffs")
        return self._process_tariffs_data(rows) if rows is not None else None

    def _parse_float(self, value_str: str) -> float | None:
        """Extract and parse float value from string."""
        if not value_str:
            return None

        try:
            return float(value_str.replace("\u00a0", "").replace(" ", "").replace(",", "."))
        except (ValueError, TypeError) as e:
            _LOGGER.warning("Failed to parse float value '%s': %s", value_str, e)
            return None

    def _parse_date(self, date_str: str, format_in: str = "%d.%m.%Y") -> str | None:
        """Parse date string and convert to ISO format (YYYY-MM-DD)."""
        try:
            return datetime.strptime(date_str.strip(), format_in).strftime("%Y-%m-%d")
        except (ValueError, AttributeError) as e:
            _LOGGER.warning("Failed to parse date '%s': %s", date_str, e)
            return None

    def _sanitize_serial_number(self, serial: str) -> str:
        """Sanitize serial number for use as key (replace non-digits with underscore)."""
        return re.sub(r"[^0-9]", "_", serial)

    def _process_meters_data(self, rows: list[list[str]]) -> dict[str, Any]:
        """Process table rows into meters data structure."""
        if not rows or len(rows) < 2:
            return {}

        meters = {}

        for row in rows[1:]:
            _LOGGER.debug("Row: %s", row)
            if len(row) < 6:
                _LOGGER.warning("Row has insufficient columns: %s", row)
                continue

            try:
                key = self._sanitize_serial_number(row[1].strip())
                meter = {
                    "name": row[0].strip(),
                    "serial_number": row[1].strip(),
                    "unit": row[2].strip(),
                    "value": self._parse_float(row[4].strip()),
                    "date": self._parse_date(row[3].strip()),
                    "next_verification_date": self._parse_date(
                        row[8].strip() if len(row) >= 9 else row[5].strip()
                    ),
                }

                _LOGGER.debug("Meter %s: %s", key, meter)
                meters[key] = meter

            except (IndexError, AttributeError) as e:
                _LOGGER.warning("Error processing row %s: %s", row, e)
                continue

        return meters

    def _process_tariffs_data(self, rows: list[list[str]]) -> dict[str, Any]:
        """Process table rows into tariffs data structure."""
        if not rows or len(rows) < 2:
            return {}

        valid_rows = [row for row in rows[1:] if len(row) >= 5]
        name_counts = Counter(row[0].strip() for row in valid_rows)
        name_occurrences: dict[str, int] = {}
        tariffs = {}

        for row in rows[1:]:
            if len(row) < 5:
                _LOGGER.warning("Tariff row has insufficient columns: %s", row)
                continue

            try:
                base_name = row[0].strip()
                name_occurrences[base_name] = name_occurrences.get(base_name, 0) + 1
                occurrence = name_occurrences[base_name]
                name = (
                    f"{base_name} №{occurrence}"
                    if name_counts[base_name] > 1
                    else base_name
                )

                tariff = {
                    "name": name,
                    "norm": self._parse_float(row[1].strip()),
                    "unit": row[2].strip(),
                    "rate_per_unit": self._parse_float(row[3].strip()),
                    "from_date": self._parse_date(row[4].strip()),
                }

                _LOGGER.debug("Processed tariff: %s", tariff)
                tariffs[name] = tariff

            except (IndexError, AttributeError) as e:
                _LOGGER.warning("Error processing tariff row %s: %s", row, e)
                continue

        return tariffs
