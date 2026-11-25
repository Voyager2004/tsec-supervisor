from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = PROJECT_ROOT / "extension"


def test_extension_is_cross_platform_manifest_v3() -> None:
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert int(manifest["minimum_chrome_version"]) <= 111
    assert "nativeMessaging" not in manifest.get("permissions", [])

    referenced_files = {
        manifest["background"]["service_worker"],
        manifest["action"]["default_popup"],
        manifest["options_page"],
    }
    for script_group in manifest["content_scripts"]:
        referenced_files.update(script_group["js"])
    for relative in referenced_files:
        assert (EXTENSION_DIR / relative).is_file(), relative

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in EXTENSION_DIR.iterdir()
        if path.suffix in {".js", ".html", ".css"}
    )
    for platform_specific in (
        "nativeMessaging",
        "osascript",
        "powershell.exe",
        "cmd.exe",
        "C:\\\\",
        "/Users/",
        "/home/",
    ):
        assert platform_specific not in source
