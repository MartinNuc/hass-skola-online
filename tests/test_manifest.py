"""The manifest is what HA and HACS validate first — keep it honest."""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "custom_components" / "skola_online" / "manifest.json"


def test_manifest_has_required_keys():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == "skola_online"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["version"]
    assert manifest["requirements"] == ["beautifulsoup4>=4.12.0"]


def test_api_package_does_not_import_home_assistant():
    """The api/ package must stay HA-agnostic so it can be tested standalone."""
    for path in (REPO_ROOT / "custom_components" / "skola_online" / "api").rglob("*.py"):
        assert "homeassistant" not in path.read_text(encoding="utf-8"), path
