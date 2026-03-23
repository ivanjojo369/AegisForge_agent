#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


VALID_TRACKS = {"purple", "openenv", "tau2", "security_arena"}


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


def get_version_from_pyproject(pyproject_path: Path) -> str | None:
    if not pyproject_path.exists() or tomllib is None:
        return None
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    project = data.get("project", {})
    version = project.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


def normalize_url(url: str) -> str:
    return url.rstrip("/")


def validate_payload(payload: dict) -> None:
    required = [
        "submission_name",
        "agent_name",
        "agent_version",
        "track",
        "public_url",
        "agent_card_url",
        "docker_image",
        "git_commit",
        "created_at_utc",
        "abstract_path",
        "readme_path",
    ]
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise SystemExit(f"Missing required submission metadata fields: {missing}")

    if payload["track"] not in VALID_TRACKS:
        raise SystemExit(f"Invalid track: {payload['track']!r}")


def main() -> int:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent.parent

    parser = argparse.ArgumentParser(description="Generate submission metadata JSON for AegisForge.")
    parser.add_argument("--submission-name", required=True)
    parser.add_argument("--track", default="purple", choices=sorted(VALID_TRACKS))
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--docker-image", required=True)
    parser.add_argument("--agent-name", default=os.environ.get("AEGISFORGE_AGENT_NAME", "AegisForge"))
    parser.add_argument("--agent-version", default=None)
    parser.add_argument("--abstract-path", default="docs/ABSTRACT.md")
    parser.add_argument("--readme-path", default="README.md")
    parser.add_argument("--notes", default="")
    parser.add_argument("--integrations", nargs="*", default=[])
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    public_url = normalize_url(args.public_url)
    pyproject_version = get_version_from_pyproject(repo_root / "pyproject.toml")
    agent_version = (
        args.agent_version
        or os.environ.get("AEGISFORGE_AGENT_VERSION")
        or pyproject_version
        or "0.1.0"
    )

    payload = {
        "submission_name": args.submission_name,
        "agent_name": args.agent_name,
        "agent_version": agent_version,
        "track": args.track,
        "public_url": public_url,
        "agent_card_url": f"{public_url}/.well-known/agent-card.json",
        "docker_image": args.docker_image,
        "git_commit": run_git(repo_root, "rev-parse", "HEAD"),
        "git_branch": run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        "created_at_utc": utc_now_iso(),
        "abstract_path": args.abstract_path,
        "readme_path": args.readme_path,
        "notes": args.notes,
        "integrations": args.integrations,
        "fair_play_acknowledged": True,
    }

    validate_payload(payload)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_output = repo_root / "artifacts" / "submission" / f"submission_{timestamp}.json"
    output_path = Path(args.output).resolve() if args.output else default_output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[prepare_submission] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
