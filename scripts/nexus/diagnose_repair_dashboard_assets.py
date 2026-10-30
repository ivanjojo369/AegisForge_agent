"""Diagnose/recover Colossus static assets after Nexus web directory migration.

Dry-run by default. --apply requires explicit operator invocation and creates
backups before any local writes. This program never starts a service or calls
Nebius/Twitch. It does not modify API routes, policies or authentication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

NAMES = ("mission_dashboard.html", "mission_dashboard.css", "mission_dashboard.js")
API = Path("src/aegisforge/nexus/mission_control/mission_http_api.py")
DEST = Path("src/aegisforge/web/nexus")
OLD_DIR = Path("src/aegisforge/nexus/mission_control/web")
OLD_ASSIGN = 'assets_root = Path(__file__).parent / "web"'
NEW_ASSIGN = 'assets_root = Path(__file__).resolve().parents[2] / "web" / "nexus"'
ASSIGN_RE = re.compile(r"^[ \t]*assets_root[ \t]*=[^\r\n]+", re.MULTILINE)
REQUIRED = {
    "mission_dashboard.html": ("mission_dashboard.js", "mission_dashboard.css", "archive-generation"),
    "mission_dashboard.css": (".monitor-grid", ".historical-grid"),
    "mission_dashboard.js": ("Bearer ${bearer}", "loadArchiveGeneration", "textContent"),
}
MAX_BYTES = 200_000


class RecoveryError(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(131072), b""):
            h.update(chunk)
    return h.hexdigest()


def allowed_path(root: Path, path: Path) -> None:
    try:
        segments = path.relative_to(root).parts
    except ValueError as exc:
        raise RecoveryError("target is outside repository") from exc
    parent = root
    if parent.is_symlink():
        raise RecoveryError("repository root is symbolic link")
    for part in segments:
        parent = parent / part
        if parent.is_symlink():
            raise RecoveryError("symbolic link in protected path: " + str(parent))


def valid_asset(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    if path.stat().st_size > MAX_BYTES:
        return False, "oversized"
    if path.stat().st_size == 0:
        return False, "empty"
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeError:
        return False, "invalid_utf8"
    if any(needle not in content for needle in REQUIRED[path.name]):
        return False, "not_recognized_dashboard_v3"
    return True, "ok"


def _module_origin() -> str | None:
    """Advisory diagnostic only; no package import is necessary."""
    try:
        spec = importlib.util.find_spec("aegisforge.nexus.mission_control.mission_http_api")
        return str(Path(spec.origin).resolve()) if spec and spec.origin else None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError, OSError):
        return None


def scan(root: Path, payload: Path, *, check_import: bool = True) -> dict:
    root = root.absolute()
    api_path = root / API
    new_dir = root / DEST
    old_dir = root / OLD_DIR
    for p in (api_path, new_dir, old_dir, *(new_dir / n for n in NAMES),
              *(old_dir / n for n in NAMES)):
        allowed_path(root, p)
    if not api_path.is_file():
        raise RecoveryError(f"missing Mission Control API: {api_path}")
    body = api_path.read_text(encoding="utf-8")
    matches = ASSIGN_RE.findall(body)
    if len(matches) != 1:
        raise RecoveryError("expected one assets_root assignment, found " + str(len(matches)))
    assignment = matches[0].strip()
    if assignment not in (OLD_ASSIGN, NEW_ASSIGN):
        raise RecoveryError("unknown assets_root assignment; no automated changes: " + assignment[:140])
    if not all(n in body for n in NAMES):
        raise RecoveryError("API does not expose the expected fixed dashboard asset filenames")
    statuses = {}
    moves = []
    for name in NAMES:
        target = new_dir / name
        old = old_dir / name
        target_ok, target_state = valid_asset(target)
        old_ok, old_state = valid_asset(old)
        backup = payload / name
        fallback_ok, fallback_state = valid_asset(backup)
        if not target_ok:
            source = old if old_ok else backup if fallback_ok else None
            if source is None:
                raise RecoveryError("no valid recovery source for " + name + "; old=" + old_state + ", payload=" + fallback_state)
            moves.append({"name": name, "source": str(source), "destination": str(target),
                          "reason": target_state})
        statuses[name] = {
            "new": target_state,
            "old": old_state,
            "new_sha256": digest(target) if target.is_file() else None,
            "old_sha256": digest(old) if old.is_file() else None,
        }
    origin = _module_origin() if check_import else None
    wrong_import = bool(origin and Path(origin).resolve() != api_path.resolve())
    return {
        "api_file": str(api_path),
        "python_import_origin": origin,
        "python_import_is_different_file": wrong_import,
        "asset_root_in_api": "old" if assignment == OLD_ASSIGN else "new",
        "actual_asset_directory_expected": str(old_dir if assignment == OLD_ASSIGN else new_dir),
        "new_asset_directory": str(new_dir),
        "assets": statuses,
        "needs_api_patch": assignment == OLD_ASSIGN,
        "assets_to_restore": moves,
        "ready_after_repair": not wrong_import,
        "api_sha256": digest(api_path),
    }


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".colossus-recover-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def run(root: Path, payload: Path, *, apply: bool = False, check_import: bool = True,
        replace_invalid: bool = False) -> dict:
    root = root.absolute()
    report = scan(root, payload, check_import=check_import)
    report["mode"] = "apply" if apply else "diagnose"
    report["completed"] = False
    report["backup_dir"] = None
    if not apply:
        return report
    if report["python_import_is_different_file"]:
        raise RecoveryError("Python imported another mission_http_api.py; fix PYTHONPATH/import before modifying files")
    invalid_existing = [r for r in report["assets_to_restore"]
                        if Path(r["destination"]).is_file() and r["reason"] != "empty"]
    if invalid_existing and not replace_invalid:
        names = ", ".join(r["name"] for r in invalid_existing)
        raise RecoveryError("unrecognized existing assets preserved: " + names +
                            "; inspect them or explicitly pass --replace-invalid")
    if not report["needs_api_patch"] and not report["assets_to_restore"]:
        report["completed"] = True
        report["note"] = "assets and path already valid; if HTTP 500 persists inspect server traceback"
        return report

    api = root / API
    original_api = api.read_bytes()
    if digest(api) != report["api_sha256"]:
        raise RecoveryError("API changed during validation; nothing written")
    originals = {}
    for move in report["assets_to_restore"]:
        dest = Path(move["destination"])
        source = Path(move["source"])
        if dest.is_file():
            originals[dest] = dest.read_bytes()
        if not valid_asset(source)[0]:
            raise RecoveryError("recovery source changed: " + str(source))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup_dir = root / ".nexus_migration_backups" / ("dashboard-recovery-" + stamp)
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(api, backup_dir / "mission_http_api.py")
    if digest(api) != digest(backup_dir / "mission_http_api.py"):
        raise RecoveryError("API backup verification failed")
    for old_file, raw in originals.items():
        atomic_write(backup_dir / old_file.name, raw)
    report["backup_dir"] = str(backup_dir)

    for move in report["assets_to_restore"]:
        src = Path(move["source"])
        dst = Path(move["destination"])
        raw = src.read_bytes()
        atomic_write(dst, raw)
        if digest(dst) != hashlib.sha256(raw).hexdigest():
            raise RecoveryError("asset verification failed; see backup: " + str(dst))

    if report["needs_api_patch"]:
        body = original_api.decode("utf-8")
        if body.count(OLD_ASSIGN) != 1:
            raise RecoveryError("API changed; restore backup")
        new_body = body.replace(OLD_ASSIGN, NEW_ASSIGN, 1)
        compile(new_body, str(api), "exec")
        if api.read_bytes() != original_api:
            raise RecoveryError("API changed during recovery; restore backup")
        atomic_write(api, new_body.encode("utf-8"))

    final = scan(root, payload, check_import=check_import)
    if final["needs_api_patch"] or final["assets_to_restore"]:
        raise RecoveryError("recovery incomplete; see backup")
    report["completed"] = True
    report["note"] = "restart server; rerun static asset tests"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--payload", type=Path, default=Path(__file__).resolve().parents[2] / "repair_payload")
    parser.add_argument("--apply", action="store_true", help="Backup and repair, otherwise report only")
    parser.add_argument("--replace-invalid", action="store_true",
                        help="Explicitly allow replacing unrecognized existing assets with verified backups")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.root, args.payload, apply=args.apply,
                             replace_invalid=args.replace_invalid), ensure_ascii=False, indent=2))
        return 0
    except (RecoveryError, OSError, UnicodeError, SyntaxError) as exc:
        print("DASHBOARD DIAGNOSIS:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
