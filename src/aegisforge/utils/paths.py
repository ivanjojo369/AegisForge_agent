from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def src_root() -> Path:
    return package_root().parent


def repo_root() -> Path:
    return package_root().parents[1]


def docs_dir() -> Path:
    return repo_root() / "docs"


def assets_dir() -> Path:
    return repo_root() / "assets"


def examples_dir() -> Path:
    return repo_root() / "examples"


def scripts_dir() -> Path:
    return repo_root() / "scripts"


def tests_dir() -> Path:
    return repo_root() / "tests"


def artifacts_dir() -> Path:
    return repo_root() / "artifacts"


def ensure_dir(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory
