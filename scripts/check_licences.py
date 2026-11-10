"""Fails if an installed Python dependency has a licence outside the allow-list.

Policy: research/LICENCES.md. Permissive licences are allowed; MPL-2.0 is allowed unmodified;
copyleft, source-available and non-commercial licences are not.
"""

import json
import subprocess
import sys

ALLOWED = (
    "MIT",
    "BSD",
    "Apache",
    "ISC",
    "PSF",
    "Python Software Foundation",
    "Mozilla Public License 2.0",
    "MPL-2.0",
    "Unlicense",
    "0BSD",
)
FORBIDDEN = ("GPL", "AGPL", "SSPL", "BUSL", "Business Source", "Elastic", "Commons Clause", "NC")
# Workspace packages are proprietary. Packages whose metadata omits the licence are listed
# here with the licence read from their repository.
REVIEWED: dict[str, str] = {
    "sentinel-core": "Proprietary (this project)",
    "sentinel-ingestion": "Proprietary (this project)",
    "sentinel-api": "Proprietary (this project)",
    "sentinel-ingest": "Proprietary (this project)",
}


def _parts(licence: str) -> list[str]:
    for separator in (" AND ", " OR ", ";"):
        licence = licence.replace(separator, "|")
    return [part.strip() for part in licence.split("|") if part.strip()]


def acceptable(licence: str) -> bool:
    parts = _parts(licence)
    if not parts:
        return False
    for part in parts:
        if any(bad in part for bad in FORBIDDEN if bad != "NC") or part.endswith("-NC"):
            return False
        if not any(ok in part for ok in ALLOWED):
            return False
    return True


def main() -> int:
    raw = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    failures = []
    packages = json.loads(raw)
    for package in packages:
        name, licence = package["Name"], package["License"]
        if name in REVIEWED or acceptable(licence):
            continue
        failures.append(f"{name} {package['Version']}: {licence}")
    if failures:
        print("Dependencies outside the licence allow-list:")
        print("\n".join(f"  {line}" for line in sorted(failures)))
        return 1
    print(f"{len(packages)} packages checked, all within the licence allow-list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
