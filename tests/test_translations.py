"""Every key the code emits (config/options steps, selector option labels)
must exist in strings.json and both shipped translations, or hassfest fails.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT = REPO_ROOT / "custom_components" / "skola_online"

STRINGS = COMPONENT / "strings.json"
EN = COMPONENT / "translations" / "en.json"
CS = COMPONENT / "translations" / "cs.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(obj, prefix: str = "") -> set[str]:
    """Every leaf key path, e.g. 'options.step.init.data.event_title'."""
    if not isinstance(obj, dict):
        return {prefix}
    keys: set[str] = set()
    for key, value in obj.items():
        keys |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return keys


def test_en_translation_is_byte_for_byte_identical_to_strings_json():
    """strings.json's own text *is* the English translation; en.json is a
    plain copy so nothing drifts between the two.
    """
    assert EN.read_text(encoding="utf-8") == STRINGS.read_text(encoding="utf-8")


def test_cs_translation_has_every_key_strings_json_has():
    assert _keys(_load(CS)) == _keys(_load(STRINGS))


def test_event_title_selector_options_are_translated_in_every_file():
    for path in (STRINGS, EN, CS):
        data = _load(path)
        assert data["options"]["step"]["init"]["data"]["event_title"]
        options = data["selector"]["event_title"]["options"]
        assert set(options) == {"full", "abbreviation", "both"}
        assert all(options.values()), f"{path}: empty selector option label"
