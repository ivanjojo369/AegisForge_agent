#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent.parent

    parser = argparse.ArgumentParser(description="Record provenance metadata for AegisForge.")
    parser.add_argument("--track", default=os.environ.get("AEGISFORGE_TRACK", "purple"))
    parser.add_argument("--public-url", default=os.environ.get("AEGISFORGE_PUBLIC_URL", ""))
    parser.add_argument("--docker-image", default=os.environ.get("AEGISFORGE_IMAGE_REF", ""))
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_output = repo_root / "artifacts" / "provenance" / f"provenance_{timestamp}.json"
    output_path = Path(args.output).resolve() if args.output else default_output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    readme_path = repo_root / "README.md"
    abstract_path = repo_root / "docs" / "ABSTRACT.md"

    payload = {
        "created_at_utc": utc_now_iso(),
        "track": args.track,
        "public_url": args.public_url,
        "docker_image": args.docker_image,
        "git": {
            "commit": run_git(repo_root, "rev-parse", "HEAD"),
            "short_commit": run_git(repo_root, "rev-parse", "--short", "HEAD"),
            "branch": run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
            "status_short": run_git(repo_root, "status", "--short"),
            "remote_origin": run_git(repo_root, "remote", "get-url", "origin"),
        },
        "runtime": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "files": {
            "README.md": {
                "path": "README.md",
                "sha256": file_sha256(readme_path),
            },
            "docs/ABSTRACT.md": {
                "path": "docs/ABSTRACT.md",
                "sha256": file_sha256(abstract_path),
            },
        },
        "environment": {
            "AEGISFORGE_AGENT_NAME": os.environ.get("AEGISFORGE_AGENT_NAME", ""),
            "AEGISFORGE_AGENT_VERSION": os.environ.get("AEGISFORGE_AGENT_VERSION", ""),
            "GITHUB_SHA": os.environ.get("GITHUB_SHA", ""),
            "GITHUB_REF_NAME": os.environ.get("GITHUB_REF_NAME", ""),
        },
    }

    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[record_provenance] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
