from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path, *, encoding: str = "utf-8") -> Any:
    file_path = Path(path)
    with file_path.open("r", encoding=encoding) as fh:
        return json.load(fh)


def dump_json(
    data: Any,
    path: str | Path,
    *,
    encoding: str = "utf-8",
    indent: int = 2,
    ensure_ascii: bool = False,
    trailing_newline: bool = True,
) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    text = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    if trailing_newline:
        text += "\n"

    file_path.write_text(text, encoding=encoding)
    return file_path


def loads_json(raw: str) -> Any:
    return json.loads(raw)


def dumps_json(
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    trailing_newline: bool = False,
) -> str:
    text = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    if trailing_newline:
        text += "\n"
    return text


def is_json_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".json"
