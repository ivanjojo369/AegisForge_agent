"""Safely repair Colossus Mission Control's static asset path after Nexus web migration.

Default mode checks only. --apply changes ONE assignment in mission_http_api.py,
with a timestamped backup. No application source files are moved or overwritten.
This script intentionally refuses unknown API layouts and incomplete asset sets.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from uuid import uuid4

ASSETS = ("mission_dashboard.html", "mission_dashboard.css", "mission_dashboard.js")
API_REL = Path("src/aegisforge/nexus/mission_control/mission_http_api.py")
ASSETS_REL = Path("src/aegisforge/web/nexus")
OLD = 'assets_root = Path(__file__).parent / "web"'
NEW = 'assets_root = Path(__file__).resolve().parents[2] / "web" / "nexus"'


class RepairError(ValueError):
    """Repair is unsafe or required files are unavailable."""


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_dir_chain(root: Path, target: Path) -> None:
    """Reject symlinks along the repo-relative path; no root escape."""
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise RepairError("path escapes repository") from exc
    node = root
    if node.is_symlink():
        raise RepairError("repository root cannot be a symlink")
    for part in relative.parts:
        node = node / part
        if node.is_symlink():
            raise RepairError(f"symlink in required path: {relative}")


def inspect(root: Path) -> dict[str, object]:
    root = root.absolute()
    api = root / API_REL
    assets_dir = root / ASSETS_REL
    for path in (api, assets_dir, *(assets_dir / name for name in ASSETS)):
        _check_dir_chain(root, path)
    if not api.is_file():
        raise RepairError(f"API not found: {api}")
    missing = [name for name in ASSETS if not (assets_dir / name).is_file()]
    if missing:
        raise RepairError("missing migrated assets in src/aegisforge/web/nexus: " + ", ".join(missing))
    with api.open("r", encoding="utf-8", newline="") as stream:
        original = stream.read()
    # Require the known static asset code. Reject unexpected duplicates or code drift.
    lines = re.findall(r"^[ \t]*assets_root[ \t]*=[^\r\n]*", original, re.MULTILINE)
    if len(lines) != 1:
        raise RepairError("expected exactly one assets_root assignment in mission_http_api.py")
    assignment = lines[0].strip()
    if assignment not in (OLD, NEW):
        raise RepairError("unknown assets_root assignment; do not patch automatically: " + assignment[:120])
    if not all(name in original for name in ASSETS):
        raise RepairError("API asset mappings differ from known Colossus version")
    return {
        "api": str(api),
        "assets_directory": str(assets_dir),
        "assets": {name: _hash(assets_dir / name) for name in ASSETS},
        "api_assignment": assignment,
        "needs_patch": assignment == OLD,
        "api_sha256_before": _hash(api),
    }


def repair(root: Path, *, apply: bool = False) -> dict[str, object]:
    report = inspect(root)
    report["mode"] = "apply" if apply else "check_only"
    report["completed"] = False
    report["backup"] = None
    if not apply:
        return report
    if not report["needs_patch"]:
        report["completed"] = True
        report["note"] = "already points to migrated web/nexus assets"
        return report

    api = Path(str(report["api"]))
    with api.open("r", encoding="utf-8", newline="") as stream:
        source = stream.read()
    if source.count(OLD) != 1:
        raise RepairError("API changed while preparing patch; not modified")
    # Verify the file was not modified between validation and application.
    if _hash(api) != report["api_sha256_before"]:
        raise RepairError("API changed during inspection; not modified")
    patched = source.replace(OLD, NEW, 1)
    compile(patched, str(api), "exec")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = api.with_name(api.name + f".backup-{stamp}-{uuid4().hex[:8]}")
    shutil.copy2(api, backup)
    if _hash(backup) != report["api_sha256_before"]:
        raise RepairError("backup hash mismatch; original API left untouched")
    report["backup"] = str(backup)
    fd, temp_name = tempfile.mkstemp(prefix=".mission_http_api-", suffix=".tmp", dir=api.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        if _hash(api) != report["api_sha256_before"]:
            raise RepairError("API changed before replace; original retained")
        os.replace(temp_name, api)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    result = inspect(root)
    if result["needs_patch"]:
        raise RepairError("post-repair validation failed; restore backup")
    report["completed"] = True
    report["api_sha256_after"] = result["api_sha256_before"]
    report["note"] = "restart Mission Control before retesting; API routes unchanged"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--apply", action="store_true", help="Back up and patch the existing API; default only inspects")
    arguments = parser.parse_args()
    try:
        print(json.dumps(repair(arguments.root, apply=arguments.apply), ensure_ascii=False, indent=2))
        return 0
    except (RepairError, OSError, UnicodeError, SyntaxError) as exc:
        print("CHECKPOINT DASHBOARD REPAIR REJECTED:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
