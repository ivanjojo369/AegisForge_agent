from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

@dataclass(slots=True)
class ArtifactRecord:
    name: str
    kind: str
    path: str
    metadata: dict[str, Any] = field(default_factory=dict)

class ArtifactWriter:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative_path: str, payload: dict[str, Any]) -> ArtifactRecord:
        path = self.root_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return ArtifactRecord(name=path.name, kind="json", path=str(path), metadata={"keys": sorted(payload.keys())})

    def write_text(self, relative_path: str, content: str, *, kind: str = "text") -> ArtifactRecord:
        path = self.root_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ArtifactRecord(name=path.name, kind=kind, path=str(path), metadata={"size": len(content)})
