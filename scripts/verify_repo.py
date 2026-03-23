#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FILES = [
    "README.md",
    "pyproject.toml",
    "Dockerfile",
    "run.sh",
    ".gitignore",
    "src/aegisforge/__init__.py",
    "src/aegisforge/agent.py",
    "src/aegisforge/executor.py",
    "src/aegisforge/a2a_server.py",
    "assets/agent-card.example.json",
    "docs/ABSTRACT.md",
]

RECOMMENDED_FILES = [
    ".env.example",
    "assets/submission_schema.json",
    "scripts/check_a2a_e2e.sh",
    "scripts/check_a2a_e2e.ps1",
    "scripts/run_local_a2a.sh",
    "scripts/run_local_a2a.ps1",
    "scripts/verify_public_endpoint.py",
    "scripts/prepare_submission.py",
    "scripts/record_provenance.py",
]

FORBIDDEN_GLOBS = [
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "*.egg-info",
]

GITIGNORE_EXPECTED_PATTERNS = [
    ".venv/",
    "__pycache__/",
    "*.egg-info/",
]


@dataclass
class CheckResult:
    errors: list[str]
    warnings: list[str]


def git_is_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=False, text=True)
        return True
    except FileNotFoundError:
        return False


def git_status_summary(repo_root: Path) -> str | None:
    if not git_is_available():
        return None
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def matches_forbidden(path: Path) -> bool:
    parts = path.parts
    if ".git" in parts:
        return False

    for pattern in FORBIDDEN_GLOBS:
        if fnmatch.fnmatch(path.name, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def find_forbidden_paths(repo_root: Path) -> list[Path]:
    found: list[Path] = []
    for path in repo_root.rglob("*"):
        if matches_forbidden(path):
            found.append(path)
    return sorted(set(found))


def check_required_files(repo_root: Path) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []

    for rel in REQUIRED_FILES:
        if not (repo_root / rel).exists():
            errors.append(f"Missing required file: {rel}")

    for rel in RECOMMENDED_FILES:
        if not (repo_root / rel).exists():
            warnings.append(f"Missing recommended file: {rel}")

    return CheckResult(errors=errors, warnings=warnings)


def check_gitignore(repo_root: Path) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []

    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        errors.append("Missing .gitignore")
        return CheckResult(errors=errors, warnings=warnings)

    content = gitignore.read_text(encoding="utf-8", errors="ignore")
    for pattern in GITIGNORE_EXPECTED_PATTERNS:
        if pattern not in content:
            warnings.append(f".gitignore does not contain recommended pattern: {pattern}")

    return CheckResult(errors=errors, warnings=warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify AegisForge repo structure and hygiene.")
    parser.add_argument("--root", default=".", help="Repo root path")
    args = parser.parse_args()

    repo_root = Path(args.root).resolve()
    if not repo_root.exists():
        raise SystemExit(f"Repo root does not exist: {repo_root}")

    errors: list[str] = []
    warnings: list[str] = []

    required = check_required_files(repo_root)
    errors.extend(required.errors)
    warnings.extend(required.warnings)

    gitignore = check_gitignore(repo_root)
    errors.extend(gitignore.errors)
    warnings.extend(gitignore.warnings)

    forbidden = find_forbidden_paths(repo_root)
    if forbidden:
        for path in forbidden:
            errors.append(f"Forbidden local artifact present: {path.relative_to(repo_root)}")

    status = git_status_summary(repo_root)
    if status:
        warnings.append("Git working tree is not clean.")
        warnings.append(status)

    print("[verify_repo] repo_root =", repo_root)

    if warnings:
        print("[verify_repo] warnings:")
        for item in warnings:
            print(f"  - {item}")

    if errors:
        print("[verify_repo] errors:")
        for item in errors:
            print(f"  - {item}")
        return 1

    print("[verify_repo] verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
