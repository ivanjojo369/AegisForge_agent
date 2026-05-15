#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""AegisForge repository unifier.

Creates a reproducible, redacted, append-only style context bundle for the
AegisForge_agent repository. The bundle is meant for review, audits,
AgentBeats Sprint 4 submission evidence, and cross-file integration work.

What it produces by default:
  artifacts/repo_unified/
    AegisForge_unified_context.md      # concatenated text context
    AegisForge_repo_manifest.json      # hashes, sizes, categories, decisions
    AegisForge_file_index.csv          # spreadsheet-friendly index
    AegisForge_sprint4_coverage.json   # 16-domain coverage scan
    AegisForge_component_map.json      # repo subsystem map
    AegisForge_repo_tree.txt           # deterministic tree
    AegisForge_unified_bundle.zip      # all generated outputs

Safety:
  - Does not execute repository code.
  - Redacts common API keys/tokens/password lines in text output.
  - Secret-like files are indexed and hashed, but their content is not emitted
    unless --include-secret-content is explicitly used.
  - Binary files are indexed and hashed, but not embedded.

Run from normal PowerShell at the repository root:
  cd C:\\Users\\PC\\Documents\\AGI-Prototipo\\AegisForge_agent
  $git = "C:\\Program Files\\Git\\cmd\\git.exe"
  python .\scripts\unify_aegisforge_repo.py --strict-sprint4

This script intentionally avoids third-party dependencies.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0-ncp-sprint4"

SPRINT4_DOMAINS: tuple[dict[str, str], ...] = (
    {"domain": "business_process", "scenario": "SaleForceOneSpy"},
    {"domain": "game", "scenario": "WikiWiper"},
    {"domain": "tau2", "scenario": "TicketTwister"},
    {"domain": "multi_agent", "scenario": "BidBot"},
    {"domain": "finance", "scenario": "TaxWizTrap"},
    {"domain": "computer_use", "scenario": "LnkLifter"},
    {"domain": "agent_safety", "scenario": "InventoryInject"},
    {"domain": "coding", "scenario": "DevContainerDoom"},
    {"domain": "cybersecurity", "scenario": "StaticShipScam"},
    {"domain": "research", "scenario": "WhistleBlowerWreck"},
    {"domain": "healthcare", "scenario": "DocuDoctor"},
    {"domain": "web", "scenario": "SearchGlitch"},
    {"domain": "agent_security", "scenario": "GymJailbreak"},
    {"domain": "software_testing", "scenario": "CodeReviewRuse"},
    {"domain": "defi", "scenario": "CryptoCrash"},
    {"domain": "legal_domain", "scenario": "LawFirmLeak"},
)

UPSTREAM_TRACKS: tuple[str, ...] = (
    "mcu",
    "mcu_minecraft",
    "officeqa",
    "crmarena",
    "crmarenapro",
    "fieldworkarena",
    "maizebargain",
    "tau2_agentbeats",
    "tau2",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
)

NCP_TRACE_CONTRACT: tuple[str, ...] = (
    "observe",
    "attend",
    "ground",
    "plan",
    "simulate",
    "act",
    "verify",
    "record",
    "scorecard",
)

SCORECARD_DIMENSIONS: tuple[str, ...] = (
    "leaderboard_performance",
    "generality",
    "cost_efficiency",
    "technical_quality",
    "innovation",
    "reproducibility",
    "fair_play",
)

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    "node_modules",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
}

DEFAULT_GENERATED_DIRS = {
    "artifacts/repo_unified",
    "artifacts/unified_repo",
    "generated_payloads",
    "htmlcov",
}

SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.prod",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "uv.lock",
}

SECRET_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".sqlite",
    ".sqlite3",
    ".db",
}

TEXT_SUFFIXES = {
    ".py", ".pyi", ".ps1", ".sh", ".bash", ".zsh", ".bat", ".cmd",
    ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".jsonl",
    ".csv", ".tsv", ".ini", ".cfg", ".conf", ".env.example",
    ".dockerfile", ".gitignore", ".dockerignore", ".in", ".lock",
    ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".xml", ".svg",
}

REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "sk-<REDACTED_OPENAI_KEY>"),
    (re.compile(r"(?i)(openai[_\- ]?api[_\- ]?key\s*[:=]\s*)([^\s\"']+)"), r"\1<REDACTED_OPENAI_API_KEY>"),
    (re.compile(r"(?i)(api[_\- ]?key\s*[:=]\s*)([^\s\"']+)"), r"\1<REDACTED_API_KEY>"),
    (re.compile(r"(?i)(password\s*[:=]\s*)([^\s\"']+)"), r"\1<REDACTED_PASSWORD>"),
    (re.compile(r"(?i)(secret\s*[:=]\s*)([^\s\"']+)"), r"\1<REDACTED_SECRET>"),
    (re.compile(r"(?i)(token\s*[:=]\s*)([^\s\"']+)"), r"\1<REDACTED_TOKEN>"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_<REDACTED>"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_<REDACTED>"),
    (re.compile(r"hf_[A-Za-z0-9]{20,}"), "hf_<REDACTED>"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{15,}"), r"\1<REDACTED_BEARER_TOKEN>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "<REDACTED_PRIVATE_KEY_BLOCK>"),
)


@dataclass
class FileRecord:
    path: str
    category: str
    size_bytes: int
    sha256: str
    is_text: bool
    is_secret_like: bool
    emitted_content: bool
    redacted: bool
    line_count: int | None = None
    reason: str = ""
    component: str = ""


@dataclass
class RepoSummary:
    script_version: str
    generated_at_utc: str
    repo_root: str
    total_files: int
    text_files: int
    binary_or_metadata_only_files: int
    emitted_text_files: int
    redacted_files: int
    total_bytes: int
    output_dir: str
    sprint4_complete: bool
    missing_sprint4_scenarios: list[str] = field(default_factory=list)
    missing_sprint4_domains: list[str] = field(default_factory=list)


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def should_skip_dir(rel: str, include_generated: bool) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(part in DEFAULT_EXCLUDE_DIRS for part in parts):
        return True
    if not include_generated:
        for generated in DEFAULT_GENERATED_DIRS:
            if rel == generated or rel.startswith(generated + "/"):
                return True
    return False


def is_secret_like(path: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in SECRET_FILE_NAMES:
        return True
    if suffix in SECRET_SUFFIXES:
        return True
    if name.endswith(".env") and name != ".env.example":
        return True
    return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def looks_like_text(path: Path, sample: bytes) -> bool:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in TEXT_SUFFIXES or name in {"dockerfile", "makefile", "readme", "license", "requirements"}:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            sample.decode("cp1252")
            return True
        except UnicodeDecodeError:
            return False


def read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def redact_text(text: str) -> tuple[str, bool]:
    redacted = False
    output = text
    for pattern, repl in REDACTION_PATTERNS:
        new_output = pattern.sub(repl, output)
        if new_output != output:
            redacted = True
            output = new_output
    return output, redacted


def classify_component(rel: str) -> str:
    p = rel.lower()
    if p.startswith("src/aegisforge/cognitive/"):
        return "ncp_cognitive_core"
    if p.startswith("src/aegisforge/orchestration/"):
        return "orchestration"
    if p.startswith("src/aegisforge/telemetry/"):
        return "telemetry_trace_ledger"
    if p.startswith("src/aegisforge/security/"):
        return "security_fair_play"
    if p.startswith("src/aegisforge/strategy/"):
        return "strategy_router_planner"
    if p.startswith("src/aegisforge/adapters/"):
        return "adapters"
    if p.startswith("integrations/openenv/"):
        return "openenv_integration"
    if p.startswith("integrations/security_arena/"):
        return "security_arena_integration"
    if p.startswith("integrations/tau2/"):
        return "tau2_integration"
    if p.startswith("data/tau2/quipu_lab/"):
        return "quipu_lab_data"
    if p.startswith("harness/") or p.startswith("src/aegisforge_eval/"):
        return "evaluation_harness"
    if p.startswith("templates/"):
        return "templates"
    if p.startswith("tests/"):
        return "tests"
    if p.startswith("scripts/"):
        return "scripts"
    if p.startswith("tooling/"):
        return "tooling"
    if p.startswith("docs/"):
        return "docs"
    if p.startswith("artifacts/"):
        return "artifacts"
    if p.startswith(".github/"):
        return "github_ci"
    return "root_or_misc"


def category_for(rel: str, path: Path) -> str:
    suffix = path.suffix.lower()
    if rel.startswith("src/"):
        return "source"
    if rel.startswith("tests/"):
        return "test"
    if rel.startswith("scripts/"):
        return "script"
    if rel.startswith("integrations/"):
        return "integration"
    if rel.startswith("templates/"):
        return "template"
    if rel.startswith("docs/") or suffix in {".md", ".rst"}:
        return "documentation"
    if rel.startswith("data/") or suffix in {".json", ".jsonl", ".csv", ".toml", ".yaml", ".yml"}:
        return "data_or_config"
    if rel.startswith("tooling/"):
        return "tooling"
    return "misc"


def build_repo_tree(records: list[FileRecord]) -> str:
    lines = ["."]
    for path in sorted(record.path for record in records):
        parts = path.split("/")
        indent = "  " * len(parts)
        lines.append(f"{indent}{parts[-1]}")
    return "\n".join(lines) + "\n"


def scan_files(root: Path, include_generated: bool) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        kept_dirnames = []
        for dirname in sorted(dirnames):
            candidate = current / dirname
            rel = normalize_rel(candidate, root)
            if should_skip_dir(rel, include_generated=include_generated):
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        for filename in sorted(filenames):
            path = current / filename
            rel = normalize_rel(path, root)
            parent_rel = "/".join(rel.split("/")[:-1])
            if parent_rel and should_skip_dir(parent_rel, include_generated=include_generated):
                continue
            files.append(path)
    return sorted(files, key=lambda p: normalize_rel(p, root).lower())


def sprint4_scan(text_by_path: dict[str, str]) -> dict[str, Any]:
    joined = "\n".join(text_by_path.values()).lower()
    scenario_hits: dict[str, dict[str, Any]] = {}
    for item in SPRINT4_DOMAINS:
        domain = item["domain"]
        scenario = item["scenario"]
        aliases = {
            scenario.lower(),
            re.sub(r"[^a-z0-9]+", "", scenario.lower()),
            domain.lower(),
        }
        if scenario == "SaleForceOneSpy":
            aliases.update({"saleforceonespy", "salesforceonespy", "salesforceone"})
        if scenario == "LnkLifter":
            aliases.update({"lnklifter", "linklifter"})
        hits = sorted(alias for alias in aliases if alias and alias in joined)
        scenario_hits[scenario] = {
            "domain": domain,
            "present": bool(hits),
            "matched_aliases": hits,
        }
    missing_scenarios = [s for s, info in scenario_hits.items() if not info["present"]]
    missing_domains = [info["domain"] for info in scenario_hits.values() if not info["present"]]
    return {
        "script_version": SCRIPT_VERSION,
        "generated_at_utc": utc_now(),
        "expected_count": len(SPRINT4_DOMAINS),
        "registered_count": len(SPRINT4_DOMAINS) - len(missing_scenarios),
        "complete": not missing_scenarios,
        "missing_scenarios": missing_scenarios,
        "missing_domains": missing_domains,
        "scenarios": scenario_hits,
        "upstream_tracks": {track: {"present": track.lower() in joined} for track in UPSTREAM_TRACKS},
        "ncp_trace_contract": {stage: {"present": stage.lower() in joined} for stage in NCP_TRACE_CONTRACT},
        "scorecard_dimensions": {dimension: {"present": dimension.lower() in joined} for dimension in SCORECARD_DIMENSIONS},
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def safe_output_dir(root: Path, output_dir: str) -> Path:
    out = Path(output_dir)
    if not out.is_absolute():
        out = root / out
    out.mkdir(parents=True, exist_ok=True)
    return out.resolve()


def generate_bundle(
    *,
    root: Path,
    output_dir: Path,
    include_generated: bool,
    include_secret_content: bool,
    max_file_bytes: int,
    strict_sprint4: bool,
) -> int:
    files = scan_files(root, include_generated=include_generated)
    records: list[FileRecord] = []
    text_by_path: dict[str, str] = {}

    context_lines: list[str] = [
        "# AegisForge Unified Repository Context",
        "",
        f"- generated_at_utc: `{utc_now()}`",
        f"- script_version: `{SCRIPT_VERSION}`",
        f"- repo_root: `{root}`",
        f"- output_dir: `{output_dir}`",
        "",
        "## Purpose",
        "",
        "This bundle unifies repository text, hashes, Sprint 4/NCP coverage, and component mapping for review and reproducible evaluation evidence.",
        "",
        "## Sprint 4 canonical domains",
        "",
    ]
    for item in SPRINT4_DOMAINS:
        context_lines.append(f"- `{item['domain']}` -> `{item['scenario']}`")
    context_lines.extend([
        "",
        "## NCP trace contract",
        "",
        ", ".join(f"`{stage}`" for stage in NCP_TRACE_CONTRACT),
        "",
        "## Files",
        "",
    ])

    total_bytes = 0
    for path in files:
        rel = normalize_rel(path, root)
        size = path.stat().st_size
        total_bytes += size
        digest = sha256_file(path)
        secret_like = is_secret_like(path)
        sample = path.read_bytes()[:4096] if size else b""
        is_text = looks_like_text(path, sample)
        category = category_for(rel, path)
        component = classify_component(rel)
        emitted = False
        redacted = False
        line_count: int | None = None
        reason = ""

        if is_text:
            if secret_like and not include_secret_content:
                reason = "secret-like file indexed and hashed only"
            elif max_file_bytes > 0 and size > max_file_bytes:
                reason = f"text file larger than max_file_bytes={max_file_bytes}; indexed and hashed only"
            else:
                text, encoding = read_text(path)
                line_count = text.count("\n") + (1 if text else 0)
                text, redacted = redact_text(text)
                emitted = True
                text_by_path[rel] = text
                context_lines.extend([
                    f"### `{rel}`",
                    "",
                    f"- component: `{component}`",
                    f"- category: `{category}`",
                    f"- sha256: `{digest}`",
                    f"- size_bytes: `{size}`",
                    f"- encoding: `{encoding}`",
                    f"- redacted: `{str(redacted).lower()}`",
                    "",
                    "```text",
                    text.rstrip(),
                    "```",
                    "",
                ])
        else:
            reason = "binary or non-text file indexed and hashed only"

        records.append(FileRecord(
            path=rel,
            category=category,
            size_bytes=size,
            sha256=digest,
            is_text=is_text,
            is_secret_like=secret_like,
            emitted_content=emitted,
            redacted=redacted,
            line_count=line_count,
            reason=reason,
            component=component,
        ))

    coverage = sprint4_scan(text_by_path)
    summary = RepoSummary(
        script_version=SCRIPT_VERSION,
        generated_at_utc=utc_now(),
        repo_root=str(root),
        total_files=len(records),
        text_files=sum(1 for r in records if r.is_text),
        binary_or_metadata_only_files=sum(1 for r in records if not r.emitted_content),
        emitted_text_files=sum(1 for r in records if r.emitted_content),
        redacted_files=sum(1 for r in records if r.redacted),
        total_bytes=total_bytes,
        output_dir=str(output_dir),
        sprint4_complete=bool(coverage["complete"]),
        missing_sprint4_scenarios=list(coverage["missing_scenarios"]),
        missing_sprint4_domains=list(coverage["missing_domains"]),
    )

    component_map: dict[str, Any] = {}
    for record in records:
        item = component_map.setdefault(record.component, {"files": 0, "bytes": 0, "categories": {}})
        item["files"] += 1
        item["bytes"] += record.size_bytes
        item["categories"][record.category] = item["categories"].get(record.category, 0) + 1

    context_lines[3:3] = [
        f"- total_files: `{summary.total_files}`",
        f"- emitted_text_files: `{summary.emitted_text_files}`",
        f"- sprint4_complete: `{str(summary.sprint4_complete).lower()}`",
    ]

    manifest = {
        "summary": asdict(summary),
        "ncp_trace_contract": list(NCP_TRACE_CONTRACT),
        "scorecard_dimensions": list(SCORECARD_DIMENSIONS),
        "sprint4_domains": list(SPRINT4_DOMAINS),
        "upstream_tracks": list(UPSTREAM_TRACKS),
        "files": [asdict(record) for record in records],
    }

    context_path = output_dir / "AegisForge_unified_context.md"
    manifest_path = output_dir / "AegisForge_repo_manifest.json"
    index_path = output_dir / "AegisForge_file_index.csv"
    coverage_path = output_dir / "AegisForge_sprint4_coverage.json"
    component_path = output_dir / "AegisForge_component_map.json"
    tree_path = output_dir / "AegisForge_repo_tree.txt"
    summary_path = output_dir / "AegisForge_unified_summary.json"
    zip_path = output_dir / "AegisForge_unified_bundle.zip"

    context_path.write_text("\n".join(context_lines), encoding="utf-8")
    write_json(manifest_path, manifest)
    write_json(coverage_path, coverage)
    write_json(component_path, component_map)
    write_json(summary_path, asdict(summary))
    tree_path.write_text(build_repo_tree(records), encoding="utf-8")

    with index_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "path", "component", "category", "size_bytes", "sha256",
            "is_text", "is_secret_like", "emitted_content", "redacted",
            "line_count", "reason",
        ])
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for artifact in (context_path, manifest_path, index_path, coverage_path, component_path, tree_path, summary_path):
            zf.write(artifact, arcname=artifact.name)

    print(json.dumps(asdict(summary), indent=2, ensure_ascii=False))
    print(f"\nWrote unified bundle: {zip_path}")
    if strict_sprint4 and not summary.sprint4_complete:
        print("\nERROR: Sprint 4 coverage scan is incomplete.", file=sys.stderr)
        print("Missing scenarios:", ", ".join(summary.missing_sprint4_scenarios), file=sys.stderr)
        return 2
    return 0


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    markers = {"pyproject.toml", "README.md", ".git"}
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return current


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unify AegisForge_agent repository files into a reproducible context bundle.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--output-dir", default="artifacts/repo_unified", help="Output directory relative to repo root.")
    parser.add_argument("--include-generated", action="store_true", help="Include generated/artifact directories skipped by default.")
    parser.add_argument("--include-secret-content", action="store_true", help="Emit secret-like file content. Not recommended.")
    parser.add_argument("--max-file-bytes", type=int, default=2_000_000, help="Maximum text file size to embed. Use 0 for unlimited.")
    parser.add_argument("--strict-sprint4", action="store_true", help="Exit non-zero if the 16 Sprint 4 scenarios are not found in emitted text.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = find_repo_root(Path(args.repo_root))
    output_dir = safe_output_dir(root, args.output_dir)
    return generate_bundle(
        root=root,
        output_dir=output_dir,
        include_generated=bool(args.include_generated),
        include_secret_content=bool(args.include_secret_content),
        max_file_bytes=int(args.max_file_bytes),
        strict_sprint4=bool(args.strict_sprint4),
    )


if __name__ == "__main__":
    raise SystemExit(main())
