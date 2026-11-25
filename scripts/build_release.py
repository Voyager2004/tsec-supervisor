"""Archive the committed source tree, excluding local credentials and runtime data."""

import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    manifest = subprocess.check_output(
        ["git", "show", "HEAD:extension/manifest.json"], cwd=ROOT
    )
    version = json.loads(manifest)["version"]
    output = ROOT / "dist" / f"tsec-supervisor-release-{version}.tar.gz"
    output.parent.mkdir(exist_ok=True)
    subprocess.run(
        [
            "git",
            "archive",
            "--format=tar.gz",
            "--prefix=tsec-supervisor/",
            "-o",
            str(output),
            "HEAD",
        ],
        cwd=ROOT,
        check=True,
    )
    with tarfile.open(output) as archive:
        archive.getmember("tsec-supervisor/server/app/main.py")
        archive.getmember("tsec-supervisor/LICENSE")
    print(output)


if __name__ == "__main__":
    main()
