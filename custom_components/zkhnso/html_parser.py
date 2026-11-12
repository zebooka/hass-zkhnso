"""HTML parser utility for converting HTML responses to JSON using CSS selectors.

Example usage:

    # Simple extraction
    value = html_to_json_simple(html, "div.title", "text")
    
    # Complex nested extraction
    config = {
        "selector": "table.meters",
        "multiple": True,
        "children": {
            "id": {"selector": "td.id", "attribute": "text"},
            "value": {"selector": "td.value", "attribute": "text"},
            "unit": {"selector": "td.unit", "attribute": "text"}
        }
    }
    meters = html_to_json(html, config)
"""
from __future__ import annotations
from typing import Any, Callable
from bs4 import BeautifulSoup, Tag
import logging

_LOGGER = logging.getLogger(__name__)


def html_to_json(
    html: str,
    config: dict[str, Any],
) -> dict[str, Any] | list[dict[str, Any]] | str | None:
    try:
        soup = BeautifulSoup(html, "html.parser")
        return _extract_data(soup, config)
    except Exception as e:
        _LOGGER.error("Error parsing HTML: %s", e)
        return None


def _extract_data(
    element: BeautifulSoup | Any,
    config: dict[str, Any],
) -> dict[str, Any] | list[dict[str, Any]] | str | None:
    """Recursively extract data from HTML element based on config."""
    selector = config.get("selector")
    if not selector:
        return None

    attribute = config.get("attribute", "text")
    multiple = config.get("multiple", False)
    children = config.get("children")

    try:
        if multiple:
            elements = element.select(selector)
            if not elements:
                return []

            if children:
                return [_extract_children(elem, children) for elem in elements if elem]
            else:
                return [_get_attribute_value(elem, attribute) for elem in elements if elem]
        else:
            elem = element.select_one(selector)
            if not elem:
                return None

            if children:
                return _extract_children(elem, children)
            else:
                return _get_attribute_value(elem, attribute)
    except Exception as e:
        _LOGGER.error("Error extracting data with selector '%s': %s", selector, e)
        return None


def _extract_children(
    element: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Extract nested data from element based on children config."""
    result = {}
    for key, child_config in config.items():
        if isinstance(child_config, dict):
            result[key] = _extract_data(element, child_config)
        else:
            result[key] = _extract_data(element, {"selector": child_config})

    return result


def _get_attribute_value(element: Tag, attribute: str) -> str | None:
    """Get attribute value from element."""
    if attribute == "text":
        return extract_cell_text(element)
    elif attribute == "fulltext":
        return element.get_text(strip=True)
    elif attribute == "html":
        return str(element)
    else:
        return element.get(attribute)


def html_to_str(
    html: str,
    selector: str,
    attribute: str = "text",
) -> str | None:
    t = html_to_json(html, {"selector": selector, "attribute": attribute})
    return " ".join(t.split()) or None if isinstance(t, str) else None


def extract_cells_from_table_rows(
    html: str,
    tr_selector: str,
    page_name: str,
) -> list[list[Tag]] | None:
    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select(tr_selector)
        if not rows:
            _LOGGER.warning("No %s table rows found (%s)", page_name.casefold(), tr_selector)
            return None

        _LOGGER.debug("Soup %s table rows (%s): %s", page_name.casefold(), tr_selector, rows)
        return [row.find_all(["td", "th"]) for row in rows]
    except Exception as e:
        _LOGGER.error("Error extracting %s table rows (%s): %s", page_name.casefold(), tr_selector, e)
        return None


def extract_cell_text(cell: Tag) -> str:
    """Extract text from table cell ignoring nested element contents."""
    if cell is None:
        return ""

    direct_text_parts: list[str] = []
    for content in cell.contents:
        if isinstance(content, str):
            text = content.strip()
            if text:
                direct_text_parts.append(text)

    if direct_text_parts:
        return " ".join(direct_text_parts)

    return cell.get_text(strip=True)

def extract_cell_href(cell: Tag) -> str | None:
    a = cell.select_one("a[href]")
    return a.get("href").strip() if a else None
