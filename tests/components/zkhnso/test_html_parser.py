"""Tests for the HTML parsing helpers."""
import pytest
from bs4 import BeautifulSoup

from custom_components.zkhnso.html_parser import (
    extract_cell_href,
    extract_cell_text,
    extract_cells_from_table_rows,
    html_to_json,
    html_to_str,
)


HTML = """
<section><h1 data-id="42"> Title </h1><ul><li>A</li><li>B</li></ul>
<table id="data"><tr><th> Name </th><th> Value </th></tr>
<tr><td>Meter <span>ignored</span></td><td> 12 </td></tr></table></section>
"""


def test_string_extraction() -> None:
    """Extract the first matching text or attribute and normalize whitespace."""
    assert html_to_str(HTML, "h1") == "Title"
    assert html_to_str(HTML, "h1", attribute="data-id") == "42"
    assert html_to_str(HTML, "li") == "A"
    assert html_to_str('<p data-value="  one\n  two  ">  hello\n  world  </p>', "p") == "hello world"
    assert html_to_str('<p data-value="  one\n  two  "></p>', "p", attribute="data-value") == "one two"
    assert html_to_str(HTML, ".missing") is None
    assert html_to_str(HTML, "h1", attribute="missing") is None
    assert html_to_str("<p>  \n </p>", "p") is None


def test_multiple_extraction() -> None:
    """Multiple matches are available through the structured parser."""
    assert html_to_json(HTML, {"selector": "li", "multiple": True}) == ["A", "B"]
    assert html_to_json(HTML, {"selector": ".missing", "multiple": True}) == []


def test_nested_and_table_extraction() -> None:
    """Nested configs and table cell extraction preserve expected text."""
    assert html_to_json(
        HTML,
        {"selector": "table", "children": {"name": "td", "value": {"selector": "td:nth-of-type(2)"}}},
    ) == {"name": "Meter", "value": "12"}
    rows = extract_cells_from_table_rows(HTML, "#data tr", "Data")
    assert [[extract_cell_text(cell) for cell in row] for row in rows] == [
        ["Name", "Value"],
        ["Meter", "12"],
    ]
    assert extract_cells_from_table_rows(HTML, ".missing", "Data") is None

HTML_WITH_LINKS = """
<table class="accounts-grid soft"><tbody>
<tr><td><a href="#777"><strong>ЛС: 123</strong></a></td><td>Address 1</td></tr>
<tr><td><a href="#888">ЛС: 456</a></td><td>Address 2</td></tr>
</tbody></table>
"""

def test_links_extraction() -> None:
    rows = extract_cells_from_table_rows(HTML_WITH_LINKS, "tr", "Accounts")
    assert [(extract_cell_text(row[0]), extract_cell_href(row[0]), extract_cell_text(row[1])) for row in rows] == [
        ("ЛС: 123", "#777", "Address 1"),
        ("ЛС: 456", "#888", "Address 2"),
    ]


@pytest.mark.parametrize(("html", "expected"), [
    ('<td><a>no href</a><a href="  /first  ">first</a><a href="/second">second</a></td>', '/first'),
    ('<td><span><a href="  /nested  ">nested</a></span><a href="/next">next</a></td>', '/nested'),
    ('<td><a href="   ">empty</a><a href="/next">next</a></td>', ''),
    ('<td><a>no href</a></td>', None),
    ('<td>no links</td>', None),
])
def test_extract_cell_href(html, expected) -> None:
    cell = BeautifulSoup(html, "html.parser").select_one("td")
    assert extract_cell_href(cell) == expected
