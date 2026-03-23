#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_IGNORES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}


def should_skip(path: Path, show_hidden: bool) -> bool:
    name = path.name
    if name in DEFAULT_IGNORES:
        return True
    if not show_hidden and name.startswith(".") and name not in {".github"}:
        return True
    if name.endswith(".egg-info"):
        return True
    return False


def walk(root: Path, prefix: str, depth: int, max_depth: int, show_hidden: bool, lines: list[str]) -> None:
    if depth > max_depth:
        return

    entries = [p for p in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())) if not should_skip(p, show_hidden)]
    total = len(entries)

    for index, entry in enumerate(entries):
        connector = "└─ " if index == total - 1 else "├─ "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "   " if index == total - 1 else "│  "
            walk(entry, prefix + extension, depth + 1, max_depth, show_hidden, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a clean repo tree.")
    parser.add_argument("--root", default=".", help="Root path to print")
    parser.add_argument("--max-depth", type=int, default=4, help="Maximum depth to print")
    parser.add_argument("--show-hidden", action="store_true", help="Show hidden files and directories")
    parser.add_argument("--output", default="", help="Optional file path to save the tree")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        raise SystemExit(f"Path does not exist: {root}")

    lines = [root.name]
    walk(root, prefix="", depth=1, max_depth=args.max_depth, show_hidden=args.show_hidden, lines=lines)
    output = "\n".join(lines) + "\n"

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        print(f"[print_tree] wrote {output_path}")
    else:
        print(output, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
