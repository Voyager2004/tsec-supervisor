"""Build the participant extension for one organiser's scoreboard."""

import argparse
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    "background.js",
    "content.js",
    "page-hook.js",
    "extension.css",
    "options.html",
    "options.js",
    "popup.html",
    "popup.js",
    "README.md",
)


def server_origin(value: str) -> str:
    url = urlsplit(value)
    host = url.hostname or ""
    local = host in {"localhost", "127.0.0.1"}
    if (
        not re.fullmatch(r"[A-Za-z0-9.-]+", host)
        or url.scheme != "https"
        and not (local and url.scheme == "http")
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or "?" in value
        or "#" in value
        or value.strip() != value
        or any(ord(c) <= 32 for c in value)
    ):
        raise argparse.ArgumentTypeError("请填写 HTTPS 根地址，本机测试可使用 HTTP")
    try:
        port = url.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("端口无效") from error
    return f"{url.scheme}://{host}" + (f":{port}" if port is not None else "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", type=server_origin, required=True)
    args = parser.parse_args()
    extension = ROOT / "extension"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    manifest["homepage_url"] = args.server_url
    url = urlsplit(args.server_url)
    # Chrome host match patterns grant the origin's host, without a port.
    manifest["host_permissions"] = sorted(
        {
            manifest["content_scripts"][0]["matches"][0],
            f"{url.scheme}://{url.hostname}/*",
        }
    )
    output = ROOT / "dist" / f"tsec-supervisor-extension-{manifest['version']}.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        archive.write(ROOT / "LICENSE", "LICENSE")
        for name in ASSETS:
            archive.write(extension / name, name)
    print(f"{args.server_url} -> {output}")


if __name__ == "__main__":
    main()
