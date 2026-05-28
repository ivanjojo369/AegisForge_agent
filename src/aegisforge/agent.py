from __future__ import annotations

# Patch BrowseComp-Plus v0.2.2 diagnostics/retrieval into src/aegisforge/agent.py.
# import-safe rebuild: agent(181).py imports/froms are stored as data, not executed here.
#
# Scope:
# - Only replaces the BrowseComp-Plus specialist methods.
# - Does not modify MAizeBargAIn, Build What I Mean, OfficeQA, CRMArena, secrets,
#   authentication, or global routing order.
# - Creates a timestamped backup before writing.
#
# Run from the repo root:
#     python tools/apply_browsecomp_plus_v0_2_2_patch.py

import ast
import os
from datetime import datetime
from pathlib import Path


AGENT_PATH = Path("src/aegisforge/agent.py")


# Exact import/from lines copied from agent(181).py.
# They are inserted into the target src/aegisforge/agent.py as needed;
# they are intentionally NOT executed by this patch script, so the patch remains
# runnable from tools/ or the repository root without package-relative imports.
AGENT_181_IMPORT_LINES = (
    'import hashlib',
    'import json',
    'import logging',
    'import math',
    'import os',
    'import re',
    'import sys',
    'import zipfile',
    'from dataclasses import asdict, dataclass, is_dataclass, replace',
    'from datetime import datetime, timezone',
    'from importlib import import_module',
    'from pathlib import Path',
    'from typing import Any, Mapping, Protocol',
    'from urllib import error as urllib_error, request as urllib_request',
    'from a2a.server.tasks import TaskUpdater',
    'from a2a.types import Message, Part, TaskState, TextPart',
    'from a2a.utils import get_message_text, new_agent_text_message',
    'from .artifact_policy import ArtifactPolicy',
    'from .role_policy import RolePolicy',
    'from .strategy import BudgetGuard, BudgetStepUsage, SelfCheck, TaskClassifier, TaskPlanner, TaskRouter',
)


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"Start marker not found: {start_marker!r}")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"End marker not found after {start_marker!r}: {end_marker!r}")
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def _clean_known_bad_import_lines(text: str) -> str:
    """Repair accidental import-line corruption from earlier generated patch files."""
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "from .role_policy import RolePolicy',":
            indent = line[: len(line) - len(line.lstrip())]
            cleaned.append(indent + "from .role_policy import RolePolicy")
            continue
        cleaned.append(line)
    return "\n".join(cleaned) + ("\n" if text.endswith("\n") else "")

def ensure_agent_runtime_imports(text: str) -> str:
    """Ensure target agent.py uses the import/from block from agent(181).py.

    This patch file remains an executable standalone script.  The relative
    imports copied from agent(181).py are *not* imported here; they are only
    inserted into the target package module (`src/aegisforge/agent.py`) if they
    are missing.  This satisfies the merge requirement while avoiding a broken
    `python tools/apply_*.py` execution path.
    """
    text = _clean_known_bad_import_lines(text)
    existing = {line.strip() for line in text.splitlines() if line.strip()}
    missing = [line for line in AGENT_181_IMPORT_LINES if line.strip() not in existing]
    # Keep the specialist runtime minimum in case future agent variants have a
    # compact import layout instead of the full agent(181).py block.
    fallback_required = (
        "import json",
        "import os",
        "import re",
        "from pathlib import Path",
        "from typing import Any, Mapping",
    )
    for line in fallback_required:
        if line not in existing and line not in missing:
            missing.append(line)
    if not missing:
        return text

    lines = text.splitlines()
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    while insert_at < len(lines) and lines[insert_at].startswith("#"):
        insert_at += 1
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    while insert_at < len(lines) and lines[insert_at].startswith("from __future__ import"):
        insert_at += 1

    # If there is already an import block, append the missing agent(181) imports
    # directly after it so global constants and LOGGER setup remain below all
    # imports.
    while insert_at < len(lines) and (
        lines[insert_at].startswith("import ") or lines[insert_at].startswith("from ") or not lines[insert_at].strip()
    ):
        insert_at += 1

    insertion = list(missing)
    if insert_at > 0 and insert_at < len(lines) and lines[insert_at - 1].strip():
        insertion.append("")
    lines[insert_at:insert_at] = insertion
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


BROWSECOMP_DETECTOR = r'''
    def _is_browsecomp_plus_protocol(self, task_text: str, metadata: Mapping[str, Any] | None = None) -> bool:
        """Detect BrowseComp-Plus deep-research QA turns.

        v0.2.2 is intentionally broader than v0.1 because BrowseComp-Plus may run
        with config {} and provide only a question.  In the expected run()
        routing, MAizeBargAIn stays before BrowseComp-Plus and CRMArena stays
        after BrowseComp-Plus, so BrowseComp questions are not captured too late.
        """
        safe_metadata: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}
        combined = self._coerce_text(task_text)
        if safe_metadata:
            try:
                combined += "\n" + json.dumps(self._normalize_for_json(dict(safe_metadata)), ensure_ascii=False)[:10000]
            except Exception:
                combined += "\n" + str(dict(safe_metadata))[:10000]

        env_hint = " ".join(
            self._coerce_text(os.getenv(name, ""))
            for name in (
                "AGENTBEATS_TRACK",
                "AGENTBEATS_BENCHMARK",
                "AGENTBEATS_TASK",
                "BROWSECOMP_MODE",
                "BROWSECOMP_PLUS_MODE",
                "BROWSECOMP_CORPUS_PATH",
                "BROWSECOMP_PLUS_CORPUS_PATH",
                "BROWSECOMP_DATA_PATH",
                "BROWSECOMP_PLUS_DATA_PATH",
                "AMBER_COMPOSE_PROJECT",
                "GITHUB_WORKFLOW",
                "GITHUB_REPOSITORY",
                "GITHUB_REF",
                "AEGISFORGE_FORCE_BROWSECOMP_PLUS",
            )
        )
        lowered = f"{combined}\n{env_hint}".lower()
        norm = re.sub(r"[^a-z0-9]+", " ", lowered)

        # Runner-level BrowseComp markers must win before CRM/Salesforce hard
        # exclusions, because BrowseComp-Plus can legitimately ask about CRM-
        # like entities while still requiring final-answer-only research QA.
        forced_runner_markers = (
            "agent_mode': 'browsecomp_plus'",
            '"agent_mode": "browsecomp_plus"',
            "agent_mode: browsecomp_plus",
            "track': 'browsecomp-plus'",
            '"track": "browsecomp-plus"',
            "track: browsecomp-plus",
            "browsecomp-plus-leaderboard",
            "rdi-foundation/browsecomp-plus",
            "aegisforge_force_browsecomp_plus=1",
        )
        force_env = os.getenv("AEGISFORGE_FORCE_BROWSECOMP_PLUS", "").strip().lower() in {"1", "true", "yes", "on"}
        if force_env or any(marker in lowered for marker in forced_runner_markers):
            try:
                LOGGER.info("BROWSECOMP_PLUS_ROUTE_V0_2_2 reason=forced_runner_marker")
            except Exception:
                pass
            return True

        # Hard exclusions: do not capture literal symbolic protocols or known
        # closed/champion tracks even if the prompt is question-shaped.
        bwim_literal = (
            "respond with [build] or [ask]",
            "answer with [build] or [ask]",
            "expected [build] or [ask]",
            "output [build] or [ask]",
            "[build] or [ask]",
        )
        if "build" in lowered and "ask" in lowered and any(marker in lowered for marker in bwim_literal):
            return False
        if any(marker in lowered for marker in (
            "allocation_self",
            "payoff_matrix",
            "opponent_profile",
            "maizebargain",
            "officeqa",
            "treasury bulletin",
            "u.s. treasury",
            "federal fiscal operations",
            "salesforce",
            "crmarena",
            "crm arena",
            "protected formula",
        )):
            return False

        explicit_markers = (
            "browsecomp-plus",
            "browsecomp_plus",
            "browsecomp+",
            "browsecomp plus",
            "browsecomp",
            "fixed document corpus",
            "transparent fixed document corpus",
            "deep research agents",
            "multi-step retrieval",
            "evidence synthesis",
            "research answer benchmark",
        )
        if any(marker in lowered for marker in explicit_markers):
            try:
                LOGGER.info("BROWSECOMP_PLUS_ROUTE_V0_2_2 reason=explicit_marker")
            except Exception:
                pass
            return True
        if "browsecomp" in norm.replace(" ", ""):
            try:
                LOGGER.info("BROWSECOMP_PLUS_ROUTE_V0_2_2 reason=compact_marker")
            except Exception:
                pass
            return True

        # Metadata-only hint: useful when config is {} but wrapper still exposes
        # query/question ids.
        meta_hint = any(token in lowered for token in (
            "query_id",
            "question_id",
            "question:",
            "\"question\"",
            "'question'",
            "fixed_corpus",
            "document_corpus",
        ))

        text = self._coerce_text(task_text).strip()
        questionish = bool(re.search(
            r"(?is)(?:^|\n)\s*(?:question|query)\s*[:\-]\s*\S|"
            r"^\s*(?:what|which|who|whom|whose|when|where|why|how|identify|name|determine|find|list)\b|"
            r"\?\s*$",
            text,
        ))

        # BrowseComp-style questions are usually natural-language research
        # questions with named entities, dates, quoted titles, or source hints.
        researchish = bool(re.search(
            r"(?is)\b(?:according to|based on|source|document|corpus|article|report|paper|website|"
            r"published|released|founded|born|died|located|served|worked|played|won|"
            r"company|organization|university|city|country|film|album|book|paper|"
            r"year|date|month|day|person|author|director)\b",
            combined,
        ))
        has_specific_anchor = bool(
            re.search(r"\b(?:18|19|20)\d{2}\b", combined)
            or re.search(r'"[^"]{3,80}"|“[^”]{3,80}”|\'[^\']{3,80}\'', combined)
            or re.search(r"\b[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){1,5}\b", task_text)
        )

        # The auto-route can be disabled during cross-track smoke tests, but is
        # on by default because the public BrowseComp-Plus config may be {}.
        auto_route = os.getenv("AEGISFORGE_BROWSECOMP_PLUS_AUTO_ROUTE", "1").strip().lower() not in {"0", "false", "no", "off"}
        routed = bool(auto_route and questionish and (meta_hint or researchish or has_specific_anchor) and len(text) >= 24)
        if routed:
            try:
                LOGGER.info(
                    "BROWSECOMP_PLUS_ROUTE_V0_2_2 reason=auto_question questionish=%s meta_hint=%s researchish=%s anchor=%s chars=%s",
                    int(questionish), int(meta_hint), int(researchish), int(has_specific_anchor), len(text),
                )
            except Exception:
                pass
        return routed
'''


BROWSECOMP_RETRIEVAL = r'''
    def _browsecomp_plus_extract_context(self, task_text: str, metadata: Mapping[str, Any] | None = None) -> str:
        """Collect prompt-provided evidence without leaking labels/answers.

        v0.2.2 recursively inspects context-like fields because BrowseComp wrappers
        can hide passages under nested A2A metadata.  Answer/gold/label/reward/
        score/result/secret/token keys are intentionally ignored.
        """
        safe_metadata: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}
        parts: list[str] = []

        text = self._coerce_text(task_text).strip()
        if text:
            for label in ("context", "evidence", "documents", "sources", "passages", "corpus", "snippet", "snippets"):
                pattern = rf"(?:^|\n)\s*{label}\s*[:\-]\s*(.+?)(?=\n\s*(?:question|query|answer|final)\s*[:\-]|\Z)"
                match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
                if match:
                    chunk = self._sanitize_text(match.group(1).strip())
                    if chunk and not self._browsecomp_plus_forbidden_name(label):
                        parts.append(f"{label}: {chunk[:7000]}")

        def walk(value: Any, path: str = "", depth: int = 0) -> None:
            if depth > 5 or len("\n\n".join(parts)) > 14000:
                return
            key_l = path.lower()
            if key_l and self._browsecomp_plus_forbidden_name(key_l):
                return
            if isinstance(value, Mapping):
                for key, item in value.items():
                    key_text = self._coerce_text(key)
                    next_path = f"{path}.{key_text}" if path else key_text
                    walk(item, next_path, depth + 1)
                return
            if isinstance(value, (list, tuple, set)):
                for idx, item in enumerate(list(value)[:40]):
                    walk(item, f"{path}[{idx}]", depth + 1)
                return
            if not isinstance(value, str):
                return
            raw = value.strip()
            if len(raw) < 80:
                return
            # Accept only context-ish fields; avoid dumping arbitrary task state.
            if not re.search(r"(?i)(context|evidence|document|source|passage|corpus|snippet|content|article|text|body|page)", path):
                return
            cleaned = self._sanitize_text(raw)
            if cleaned:
                parts.append(f"{path}:\n{cleaned[:5000]}")

        if safe_metadata:
            walk(safe_metadata)
            flattened = self._browsecomp_plus_flatten_metadata(safe_metadata, limit=9000)
            if flattened:
                # Flattened metadata is useful for query extraction; keep it
                # lower priority and sanitize in case wrappers include envelope noise.
                parts.append(f"metadata:\n{self._sanitize_text(flattened)[:5000]}")

        seen: set[str] = set()
        unique_parts: list[str] = []
        for part in parts:
            key = re.sub(r"\s+", " ", part[:500]).strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique_parts.append(part)
        return "\n\n".join(unique_parts)[:16000]

    def _browsecomp_plus_query_terms(self, question: str) -> list[str]:
        """Extract high-signal terms/phrases for fixed-corpus retrieval."""
        text = self._coerce_text(question)
        terms: list[str] = []

        stop = {
            "the", "and", "for", "with", "from", "that", "this", "which", "what", "when",
            "where", "who", "whom", "whose", "how", "why", "was", "were", "are", "is",
            "does", "did", "about", "into", "over", "under", "between", "among", "after",
            "before", "using", "according", "source", "document", "documents", "corpus",
            "answer", "question", "query", "final", "only", "please", "return", "provide",
            "identify", "determine", "find", "name", "based", "reported", "following",
        }

        def add(term: str) -> None:
            term = re.sub(r"\s+", " ", term.strip().lower()).strip(" .,:;!?()[]{}\"'`")
            if len(term) < 3:
                return
            if term in stop:
                return
            if term not in terms:
                terms.append(term)

        for phrase in re.findall(r'"([^"]{3,120})"|“([^”]{3,120})”|\'([^\']{3,120})\'', text):
            add(next((p for p in phrase if p), ""))

        # Preserve named entities as phrases before tokenization.
        for phrase in re.findall(r"\b[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){1,6}\b", text):
            if not phrase.isupper():
                add(phrase)

        for year in re.findall(r"\b(?:17|18|19|20)\d{2}\b", text):
            add(year)

        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}", text.lower()):
            add(token)

        # Favor rarer/longer anchors first, while keeping enough broad tokens for
        # BM25-like matching.
        terms.sort(key=lambda t: ((" " not in t), -len(t), t))
        return terms[:32]

    def _browsecomp_plus_forbidden_name(self, name: str) -> bool:
        name_l = self._coerce_text(name).lower()
        compact = re.sub(r"[^a-z0-9]+", "_", name_l)
        forbidden = (
            "answer", "answers", "answer_key", "gold", "label", "labels",
            "reward", "score", "scores", "result", "results", "eval",
            "evaluation", "leaderboard", "submission", "ground_truth",
            "truth", "solution", "solutions", "target", "expected",
            "secret", "secrets", "credential", "credentials", "password",
            "passwd", "api_key", "apikey", "access_key", "private_key",
            "client_secret", "token", "tokens", "oauth", "bearer",
            "refresh_token", "id_token", "authorization",
        )
        return any(part in compact for part in forbidden)

    def _browsecomp_plus_candidate_roots(self) -> list[Path]:
        """Return scoped corpus roots only; never recursively scan broad workdirs."""
        env_names = (
            "BROWSECOMP_CORPUS_PATH",
            "BROWSECOMP_PLUS_CORPUS_PATH",
            "BROWSECOMP_DATA_PATH",
            "BROWSECOMP_PLUS_DATA_PATH",
            "CORPUS_PATH",
            "DOCUMENT_CORPUS_PATH",
            "DATASET_PATH",
            "DATA_PATH",
            "BENCHMARK_DATA_PATH",
        )
        scoped_static_roots = (
            "/data",
            "/dataset",
            "/datasets",
            "/corpus",
            "/app/data",
            "/app/corpus",
            "/app/dataset",
            "/workspace/data",
            "/workspace/corpus",
            "/workspace/dataset",
            "/workspace/datasets",
            "/workspace/docs",
            "/workspace/documents",
            "/workspace/sources",
            "/workspaces/data",
            "/workspaces/corpus",
            "/workspaces/dataset",
            "/workspaces/datasets",
            "/workspaces/docs",
            "/workspaces/documents",
            "/workspaces/sources",
            "/mnt/data/browsecomp",
            "/mnt/data/corpus",
            "/mnt/data/dataset",
            "/mnt/data/datasets",
            "/tmp/browsecomp",
            "/tmp/corpus",
            "/tmp/dataset",
            "/tmp/datasets",
        )
        safe_child_names = (
            "browsecomp",
            "browsecomp_plus",
            "browsecomp-plus",
            "corpus",
            "document_corpus",
            "documents",
            "docs",
            "sources",
            "passages",
            "wiki",
            "data",
            "dataset",
            "datasets",
        )
        broad_roots = {"/", "/tmp", "/workspace", "/workspaces", "/app", "/mnt", "/mnt/data"}

        roots: list[Path] = []
        seen: set[str] = set()

        def add_root(candidate: Path) -> None:
            try:
                if not candidate.exists():
                    return
                resolved_path = candidate.resolve()
                resolved = str(resolved_path)
            except Exception:
                return
            if resolved in seen:
                return
            if self._browsecomp_plus_forbidden_name(resolved_path.name) or self._browsecomp_plus_forbidden_name(resolved):
                return
            seen.add(resolved)
            roots.append(resolved_path)

        def add_candidate(raw: str) -> None:
            if not raw:
                return
            try:
                root = Path(str(raw)).expanduser()
                resolved = str(root.resolve()) if root.exists() else str(root)
            except Exception:
                return

            # If an env var accidentally points at a broad container/workspace
            # directory, search only likely corpus children instead of rglob("*")
            # over the whole directory tree.
            if resolved in broad_roots:
                for child_name in safe_child_names:
                    add_root(root / child_name)
                return
            add_root(root)

        for name in env_names:
            add_candidate(os.getenv(name, ""))
        for raw in scoped_static_roots:
            add_candidate(raw)

        # Add likely immediate child corpus folders from already-scoped roots only.
        child_name_re = re.compile(r"(?i)(browse|corpus|document|docs|source|passage|wiki|data|dataset)")
        for root in list(roots)[:14]:
            if not root.is_dir():
                continue
            try:
                for child in list(root.iterdir())[:80]:
                    if not child.exists() or self._browsecomp_plus_forbidden_name(child.name):
                        continue
                    if child.is_dir() and child_name_re.search(child.name):
                        add_root(child)
            except Exception:
                continue

        return roots[:24]

    def _browsecomp_plus_text_from_jsonish(self, raw: str, source_name: str) -> list[str]:
        """Return textual records from JSON/JSONL without using answer labels."""
        records: list[str] = []

        def flatten(value: Any, path: str = "", depth: int = 0) -> str:
            if depth > 5:
                return ""
            if path and self._browsecomp_plus_forbidden_name(path):
                return ""
            if isinstance(value, str):
                return self._sanitize_text(value)
            if isinstance(value, (int, float, bool)):
                return str(value)
            if isinstance(value, Mapping):
                chunks: list[str] = []
                for key, item in value.items():
                    key_text = self._coerce_text(key)
                    if self._browsecomp_plus_forbidden_name(key_text):
                        continue
                    sub = flatten(item, f"{path}.{key_text}" if path else key_text, depth + 1)
                    if sub:
                        chunks.append(f"{key_text}: {sub}")
                return "\n".join(chunks)
            if isinstance(value, (list, tuple)):
                chunks = [flatten(item, path, depth + 1) for item in list(value)[:40]]
                return "\n".join(chunk for chunk in chunks if chunk)
            return ""

        try:
            obj = json.loads(raw)
            if isinstance(obj, list):
                for item in obj[:120]:
                    flat = flatten(item)
                    if flat:
                        records.append(flat)
            else:
                flat = flatten(obj)
                if flat:
                    records.append(flat)
        except Exception:
            # JSONL fallback.
            for line in raw.splitlines()[:2000]:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                flat = flatten(obj)
                if flat:
                    records.append(flat)
        return records[:200]

    def _browsecomp_plus_score_text(self, text: str, source_name: str, terms: list[str]) -> int:
        low = text.lower()
        name_l = source_name.lower()
        score = 0
        for term in terms:
            if not term:
                continue
            term_l = term.lower()
            if " " in term_l:
                if term_l in low:
                    score += 9
                if term_l in name_l:
                    score += 12
            else:
                # Word-ish match for short tokens; substring for long IDs/names.
                if len(term_l) <= 4:
                    count = len(re.findall(rf"(?<![a-z0-9]){re.escape(term_l)}(?![a-z0-9])", low))
                else:
                    count = low.count(term_l)
                if count:
                    score += min(8, count) * 2
                if term_l in name_l:
                    score += 5
        if re.search(r"(?i)\b(title|author|date|published|source|url|content|body|text)\b", text[:1000]):
            score += 2
        return score

    def _browsecomp_plus_best_window(self, text: str, terms: list[str], radius: int = 1800) -> str:
        raw = self._sanitize_text(text)
        low = raw.lower()
        positions: list[int] = []
        for term in terms:
            pos = low.find(term.lower())
            if pos >= 0:
                positions.append(pos)
        if not positions:
            return raw[: min(len(raw), radius * 2)]
        center = min(positions)
        start = max(0, center - radius)
        end = min(len(raw), center + radius * 2)
        # Try to start/end on sentence-ish boundaries.
        if start > 0:
            dot = raw.find(". ", start, min(center, start + 400))
            if dot >= 0:
                start = dot + 2
        if end < len(raw):
            dot = raw.rfind(". ", max(center, end - 500), end)
            if dot >= 0:
                end = dot + 1
        return re.sub(r"\s+", " ", raw[start:end]).strip()

    def _browsecomp_plus_local_evidence(self, question: str) -> str:
        """Retrieve safe evidence from mounted BrowseComp-Plus corpus.

        v0.2.2 diagnostics:
        - probes env roots plus scoped corpus/data mount roots only;
        - supports txt/md/json/jsonl/csv/tsv/html and zip archives;
        - rejects answer/gold/label/reward/score/result/eval/secret/token paths and JSON keys;
        - records scan diagnostics in self._browsecomp_plus_last_diag;
        - returns only source snippets, never labels or answer keys.
        """
        from datetime import datetime as _bc_datetime, timezone as _bc_timezone
        import zipfile as _bc_zipfile

        started = _bc_datetime.now(_bc_timezone.utc)
        terms = self._browsecomp_plus_query_terms(question)
        diag: dict[str, Any] = {
            "mode": "browsecomp_plus_retrieval_v0_2_2",
            "terms": terms[:12],
            "roots_seen": 0,
            "root_names": [],
            "files_seen": 0,
            "archives_seen": 0,
            "records_seen": 0,
            "forbidden_skips": 0,
            "read_errors": 0,
            "scored_hits": 0,
            "max_score": 0,
            "top_sources": [],
            "elapsed_ms": 0,
        }
        self._browsecomp_plus_last_diag = diag
        if not terms:
            return ""

        roots = self._browsecomp_plus_candidate_roots()
        diag["roots_seen"] = len(roots)
        diag["root_names"] = [str(root)[:160] for root in roots[:12]]

        suffixes = {".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".html", ".htm"}
        archive_suffixes = {".zip"}
        scan_limit_raw = os.getenv("AEGISFORGE_BROWSECOMP_SCAN_LIMIT", "1400")
        try:
            scan_limit = max(200, min(3000, int(scan_limit_raw)))
        except Exception:
            scan_limit = 1400
        per_file_limit = 650_000
        scored: list[tuple[int, str, str]] = []

        def consider_text(source: str, raw_text: str) -> None:
            if not raw_text:
                return
            if self._browsecomp_plus_forbidden_name(source):
                diag["forbidden_skips"] += 1
                return

            records = [raw_text]
            suffix = Path(source).suffix.lower()
            if suffix in {".json", ".jsonl"}:
                records = self._browsecomp_plus_text_from_jsonish(raw_text, source) or [raw_text]

            for idx, record in enumerate(records[:220]):
                if len(record.strip()) < 40:
                    continue
                diag["records_seen"] += 1
                score = self._browsecomp_plus_score_text(record, source, terms)
                if score <= 0:
                    continue
                window = self._browsecomp_plus_best_window(record, terms)
                if not window:
                    continue
                diag["scored_hits"] += 1
                diag["max_score"] = max(int(diag["max_score"]), score)
                label = Path(source).name
                if len(records) > 1:
                    label = f"{label}#record{idx}"
                scored.append((score, source, window[:3600]))

        for root in roots:
            try:
                iterator = root.rglob("*") if root.is_dir() else iter([root])
                for path in iterator:
                    if diag["files_seen"] >= scan_limit:
                        break
                    try:
                        if not path.is_file():
                            continue
                        if self._browsecomp_plus_forbidden_name(str(path)):
                            diag["forbidden_skips"] += 1
                            continue
                        suffix = path.suffix.lower()
                        if suffix not in suffixes and suffix not in archive_suffixes:
                            continue
                        diag["files_seen"] += 1

                        if suffix in archive_suffixes:
                            diag["archives_seen"] += 1
                            with _bc_zipfile.ZipFile(path) as zf:
                                for member in zf.infolist()[:500]:
                                    if member.is_dir():
                                        continue
                                    member_name = member.filename
                                    if self._browsecomp_plus_forbidden_name(member_name):
                                        diag["forbidden_skips"] += 1
                                        continue
                                    if Path(member_name).suffix.lower() not in suffixes:
                                        continue
                                    try:
                                        raw_bytes = zf.read(member)[:per_file_limit]
                                        raw = raw_bytes.decode("utf-8", errors="ignore")
                                    except Exception:
                                        diag["read_errors"] += 1
                                        continue
                                    consider_text(f"{path.name}!{member_name}", raw)
                            continue

                        raw = path.read_text(encoding="utf-8", errors="ignore")[:per_file_limit]
                        consider_text(str(path), raw)
                    except Exception:
                        diag["read_errors"] += 1
                        continue
            except Exception:
                diag["read_errors"] += 1
                continue

        if not scored:
            diag["elapsed_ms"] = int((_bc_datetime.now(_bc_timezone.utc) - started).total_seconds() * 1000)
            try:
                LOGGER.info(
                    "BROWSECOMP_PLUS_DIAG_V0_2_2 roots=%s files=%s archives=%s records=%s hits=0 skips=%s errors=%s elapsed_ms=%s",
                    diag["roots_seen"], diag["files_seen"], diag["archives_seen"], diag["records_seen"],
                    diag["forbidden_skips"], diag["read_errors"], diag["elapsed_ms"],
                )
            except Exception:
                pass
            return ""

        scored.sort(key=lambda item: item[0], reverse=True)
        chunks: list[str] = []
        top_sources: list[str] = []
        seen_windows: set[str] = set()
        for score, source, snippet in scored[:10]:
            dedupe = re.sub(r"\W+", " ", snippet[:300]).strip().lower()
            if dedupe in seen_windows:
                continue
            seen_windows.add(dedupe)
            source_name = Path(source.split("!", 1)[0]).name
            top_sources.append(source_name[:120])
            chunks.append(f"[source: {source_name}; score={score}] {snippet}")
            if len("\n\n".join(chunks)) >= 12000:
                break

        diag["top_sources"] = top_sources[:8]
        diag["elapsed_ms"] = int((_bc_datetime.now(_bc_timezone.utc) - started).total_seconds() * 1000)
        try:
            LOGGER.info(
                "BROWSECOMP_PLUS_DIAG_V0_2_2 roots=%s files=%s archives=%s records=%s hits=%s max_score=%s skips=%s errors=%s evidence_chars=%s elapsed_ms=%s top_sources=%s",
                diag["roots_seen"], diag["files_seen"], diag["archives_seen"], diag["records_seen"],
                diag["scored_hits"], diag["max_score"], diag["forbidden_skips"], diag["read_errors"],
                len("\n\n".join(chunks)), diag["elapsed_ms"], "|".join(top_sources[:5]),
            )
        except Exception:
            pass
        return "\n\n".join(chunks)[:12000]
'''


BROWSECOMP_HANDLER = r'''
    def _handle_browsecomp_plus_turn(self, task_text: str, metadata: Mapping[str, Any]) -> str:
        """BrowseComp-Plus v0.2.2 diagnostic/retrieval specialist.

        Final channel stays answer-only, while diagnostics go into logs and
        self._browsecomp_plus_last_status for post-run inspection.
        """
        question = self._browsecomp_plus_extract_question(task_text, metadata)
        context = self._browsecomp_plus_extract_context(task_text, metadata)
        local_evidence = self._browsecomp_plus_local_evidence(question or task_text)
        retrieval_diag = dict(getattr(self, "_browsecomp_plus_last_diag", {}) or {})

        if local_evidence:
            context = (context + "\n\nretrieved fixed-corpus evidence:\n" + local_evidence).strip()

        has_prompt_context = bool(context and not local_evidence) or bool(context and "metadata:" in context.lower())
        system_prompt = (
            "You are the BrowseComp-Plus fixed-corpus research answer specialist. "
            "Answer with ONLY the final answer string. Use the provided context and retrieved evidence. "
            "Do not include reasoning, citations, source names, markdown, caveats, or benchmark/protocol text. "
            "If evidence is incomplete, infer the most likely concise answer from the supplied snippets; "
            "avoid generic phrases such as INSUFFICIENT_INFORMATION unless absolutely no clue exists."
        )
        user_prompt = f"Question:\n{question or task_text[:1600].strip()}\n"
        if context:
            user_prompt += f"\nContext/evidence:\n{context[:18000]}\n"
        else:
            user_prompt += "\nNo explicit context was supplied to the agent. Use the question itself and answer concisely if possible.\n"
        user_prompt += "\nFinal answer only:"

        llm_text = self._call_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=160,
        )
        answer = self._browsecomp_plus_finalize_answer(llm_text, question=question)

        # Context-only fallback: when the LLM endpoint fails/rate-limits but the
        # prompt carries a clear direct field, extract it without touching gold
        # files.  This is intentionally conservative.
        if not answer or answer == "INSUFFICIENT_INFORMATION":
            inline = ""
            combined = f"{task_text}\n{self._browsecomp_plus_flatten_metadata(metadata, limit=5000)}"
            # Do not trust expected/gold labels.  Only accept user-facing fields
            # that look like supplied response text, not evaluator labels.
            for pattern in (
                r"(?:final_response|response_text|short_answer|observed_answer)\s*[:\-]\s*([^\n]{1,220})",
                r"(?:answer\s+candidate|candidate_answer)\s*[:\-]\s*([^\n]{1,220})",
            ):
                match = re.search(pattern, combined, flags=re.IGNORECASE)
                if match and not self._browsecomp_plus_forbidden_name(match.group(0).split(":", 1)[0]):
                    inline = match.group(1).strip()
                    break
            answer = self._browsecomp_plus_finalize_answer(inline, question=question) if inline else ""

        if not answer:
            answer = "INSUFFICIENT_INFORMATION"

        self._browsecomp_plus_last_status = {
            "mode": "browsecomp_plus_diagnostic_retrieval_v0_2_2",
            "question_chars": len(question),
            "context_chars": len(context),
            "prompt_context_present": bool(has_prompt_context),
            "local_evidence_present": bool(local_evidence),
            "local_evidence_chars": len(local_evidence),
            "retrieval_diag": self._normalize_for_json(retrieval_diag),
            "llm_calls_used": self._current_llm_calls,
            "llm_error": getattr(self, "_last_llm_error", ""),
            "answer_chars": len(answer),
        }
        try:
            LOGGER.info(
                "BROWSECOMP_PLUS_STATUS_V0_2_2 question_chars=%s context_chars=%s local_evidence=%s local_chars=%s llm_calls=%s llm_error=%s answer_chars=%s roots=%s files=%s hits=%s",
                len(question), len(context), int(bool(local_evidence)), len(local_evidence),
                self._current_llm_calls, getattr(self, "_last_llm_error", ""), len(answer),
                retrieval_diag.get("roots_seen", 0), retrieval_diag.get("files_seen", 0), retrieval_diag.get("scored_hits", 0),
            )
        except Exception:
            pass
        return answer
'''



PUBLIC_EXPORTS_BLOCK = '\n\n# ---------------------------------------------------------------------------\n# Public compatibility exports\n# ---------------------------------------------------------------------------\n# Some A2A/server tests import either `AegisForgeAgent` or `Agent` from this\n# module.  Keep the canonical class and expose the historical alias explicitly.\nAgent = AegisForgeAgent\n\n__all__ = [\n    "AegisForgeAgent",\n    "Agent",\n    "get_message_text",\n    "new_agent_text_message",\n]\n'


def _ensure_public_agent_exports(text: str) -> str:
    """Expose Agent alias and public helpers expected by tests/loaders."""
    if "class AegisForgeAgent" not in text:
        return text
    updated = text.rstrip()
    if "Agent = AegisForgeAgent" not in updated:
        updated += PUBLIC_EXPORTS_BLOCK
    if "__all__" not in updated[-1400:]:
        updated += '\n\n__all__ = ["AegisForgeAgent", "Agent", "get_message_text", "new_agent_text_message"]\n'
    return updated + ("\n" if text.endswith("\n") else "")


def _validate_target_agent_shape(text: str) -> None:
    """Fail fast if src/aegisforge/agent.py was accidentally replaced by a patch script."""
    required = ("class AegisForgeAgent", "async def run", "def _handle_browsecomp_plus_turn")
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise SystemExit(
            "Refusing to patch src/aegisforge/agent.py because it does not look like the complete agent module. "
            "Missing: " + ", ".join(missing) + ". Restore/copy a complete agent.py first, then rerun this patch."
        )


def main() -> None:
    if not AGENT_PATH.exists():
        raise SystemExit(f"Not found: {AGENT_PATH}. Run this from the AegisForge_agent repo root.")

    text = AGENT_PATH.read_text(encoding="utf-8")
    force = os.getenv("AEGISFORGE_BROWSECOMP_PLUS_PATCH_FORCE", "0").strip().lower() in {"1", "true", "yes", "on"}
    if "browsecomp_plus_diagnostic_retrieval_v0_2_2" in text and not force:
        print("BrowseComp-Plus v0.2.2 patch already appears to be installed. Set AEGISFORGE_BROWSECOMP_PLUS_PATCH_FORCE=1 to re-apply.")
        return

    _validate_target_agent_shape(text)
    original = text
    text = ensure_agent_runtime_imports(text)
    text = replace_between(
        text,
        "    def _is_browsecomp_plus_protocol(self, task_text: str, metadata: Mapping[str, Any] | None = None) -> bool:",
        "    def _browsecomp_plus_flatten_metadata(self, value: Any, *, depth: int = 0, limit: int = 10000) -> str:",
        BROWSECOMP_DETECTOR,
    )
    text = replace_between(
        text,
        "    def _browsecomp_plus_extract_context(self, task_text: str, metadata: Mapping[str, Any] | None = None) -> str:",
        "    def _browsecomp_plus_finalize_answer(self, text: str, *, question: str = \"\") -> str:",
        BROWSECOMP_RETRIEVAL,
    )
    text = replace_between(
        text,
        "    def _handle_browsecomp_plus_turn(self, task_text: str, metadata: Mapping[str, Any]) -> str:",
        "    async def run(self, message: Message, updater: TaskUpdater) -> None:",
        BROWSECOMP_HANDLER,
    )

    text = _ensure_public_agent_exports(text)

    try:
        ast.parse(text)
        compile(text, str(AGENT_PATH), "exec")
    except SyntaxError as exc:
        raise SystemExit(f"Patched agent.py failed syntax validation: {exc}") from exc

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = AGENT_PATH.with_suffix(f".py.bak_browsecomp_v0_2_2_{timestamp}")
    backup.write_text(original, encoding="utf-8")
    AGENT_PATH.write_text(text, encoding="utf-8")

    print(f"Patched: {AGENT_PATH}")
    print(f"Backup:  {backup}")
    print("Installed: browsecomp_plus_diagnostic_retrieval_v0_2_2 (agent183 safe rebuild + public exports)")


if __name__ == "__main__":
    main()
