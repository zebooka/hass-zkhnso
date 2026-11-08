"""Tests for translation consistency."""
import json
from pathlib import Path
from string import Formatter

import pytest

from custom_components.zkhnso import config_flow


def _flatten_translations(value, prefix=""):
    """Map translation paths to their text."""
    if isinstance(value, dict):
        return {
            path: text
            for key, item in value.items()
            for path, text in _flatten_translations(item, f"{prefix}.{key}" if prefix else key).items()
        }
    return {prefix: value}


def test_english_and_russian_translations_match() -> None:
    """Both languages provide the same keys and formatting placeholders."""
    translations_dir = Path(config_flow.__file__).parent / "translations"
    translations = {
        language: _flatten_translations(json.loads(
            (translations_dir / f"{language}.json").read_text(encoding="utf-8")
        ))
        for language in ("en", "ru")
    }
    english, russian = translations["en"], translations["ru"]
    assert english.keys() == russian.keys()

    for path, text in english.items():
        english_fields = {field for _, field, _, _ in Formatter().parse(text) if field is not None}
        russian_fields = {field for _, field, _, _ in Formatter().parse(russian[path]) if field is not None}
        assert english_fields == russian_fields, path


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ru", "Настройки ЖКХНСО обновлены"), ("en", "ZKHNSO settings were updated")],
)
def test_reconfigure_success_message_is_translated(language, expected) -> None:
    """Successful reconfiguration has a localized abort message."""
    translations_dir = Path(config_flow.__file__).parent / "translations"
    translation = json.loads((translations_dir / f"{language}.json").read_text(encoding="utf-8"))
    assert translation["config"]["abort"]["reconfigure_successful"] == expected
