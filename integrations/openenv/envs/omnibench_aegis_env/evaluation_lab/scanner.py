from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


ALLOWED_HOST = "github.com"
MAX_FILES = 1500
MAX_FILE_BYTES = 512_000
CLASSIFIER_VERSION = "0.1.2"

# Assignment-style secret detector.  It intentionally does *not* treat every
# variable containing "token" as a secret; key filtering below decides whether a
# matched assignment is sensitive.
KEY_VALUE_RE = re.compile(
    r"""(?ix)
    (?P<key>\b[A-Za-z_][A-Za-z0-9_.-]{1,100}\b)
    \s*(?::\s*[^=,\n#]+)?
    \s*=\s*
    (?P<value>
        "(?:[^"\\]|\\.)*" |
        '(?:[^'\\]|\\.)*' |
        [^,\n#)\]}]+ 
    )
    """
)

KEY_COLON_RE = re.compile(
    r"""(?ix)
    (?P<key>\b[A-Za-z_][A-Za-z0-9_.-]{1,100}\b)
    \s*:\s*
    (?P<value>
        "(?:[^"\\]|\\.)*" |
        '(?:[^'\\]|\\.)*' |
        [^,\n#)\]}]+ 
    )
    """
)

AUTH_BEARER_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+)(?P<value>[A-Za-z0-9._~+/=-]{12,})"
)

PLACEHOLDER_SECRET_RE = re.compile(
    r"""(?ix)
    ^(
        <[^>]+>|
        \$\{[^}]+\}|
        \$\{\{[^}]+\}\}|
        your[_-]?.*|
        example.*|
        sample.*|
        dummy.*|
        fake.*|
        test.*|
        placeholder.*|
        changeme|
        change[_-]?me|
        none|null|nil|todo|xxx+|\.\.\.|-|
        true|false
    )$
    """
)

# Token-like names that commonly appear in ML/evaluation/game code and should
# not be treated as credentials.
SAFE_TOKEN_KEYS = {
    "token",
    "tokens",
    "max_token",
    "max_tokens",
    "min_tokens",
    "total_token",
    "total_tokens",
    "prompt_token",
    "prompt_tokens",
    "completion_token",
    "completion_tokens",
    "pred_token",
    "pred_tokens",
    "gold_token",
    "gold_tokens",
    "input_token",
    "input_tokens",
    "output_token",
    "output_tokens",
    "num_token",
    "num_tokens",
    "n_token",
    "n_tokens",
    "life_token",
    "life_tokens",
    "info_token",
    "info_tokens",
    "token_budget",
    "token_count",
    "token_counts",
    "token_efficiency",
    "agree_token",
}

SECRET_TOKEN_KEYS = {
    "auth_token",
    "access_token",
    "refresh_token",
    "bearer_token",
    "session_token",
    "id_token",
    "csrf_token",
    "xsrf_token",
    "jwt_token",
    "github_token",
    "gh_token",
    "ghcr_token",
    "hf_token",
    "huggingface_token",
    "api_token",
    "client_token",
    "webhook_token",
}

RISK_PATTERNS = {
    "ci_broad_permissions": [
        r"permissions:\s*write-all",
        r"contents:\s*write",
        r"actions:\s*write",
        r"id-token:\s*write",
    ],
    "dangerous_shell_download": [
        r"curl\s+.*\|\s*(bash|sh)",
        r"wget\s+.*\|\s*(bash|sh)",
    ],
    "docker_privilege_risk": [
        r"--privileged",
        r"USER\s+root",
        r"docker\.sock",
        r"/var/run/docker\.sock",
        r"--network=host",
    ],
    "agent_surface_risk": [
        r"\bA2A\b",
        r"\bAgentCard\b",
        r"\bTaskUpdater\b",
        r"\bDataPart\b",
        r"\bFilePart\b",
        r"\bFileWithBytes\b",
    ],
}


@dataclass(frozen=True)
class Finding:
    category: str
    severity: str
    file: str
    line: int
    evidence: str
    recommendation: str


def validate_public_github_url(repo_url: str) -> str:
    parsed = urlparse(repo_url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != ALLOWED_HOST:
        raise ValueError("Only public https://github.com/<owner>/<repo> URLs are allowed.")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("Expected GitHub URL format: https://github.com/<owner>/<repo>")

    owner, repo = parts[0], parts[1].replace(".git", "")
    return f"https://github.com/{owner}/{repo}.git"


def clone_read_only(repo_url: str) -> Path:
    safe_url = validate_public_github_url(repo_url)
    tmpdir = Path(tempfile.mkdtemp(prefix="aegisforge_eval_"))

    subprocess.run(
        ["git", "clone", "--depth", "1", "--no-tags", safe_url, str(tmpdir / "repo")],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90,
    )

    return tmpdir / "repo"


def normalize_repo_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")


def normalize_key(key: str) -> str:
    key = str(key).strip().replace("-", "_")
    key = key.split(".")[-1]
    return re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_")


def iter_text_files(repo_path: Path):
    """Compatibility generator used by older tests.

    New scan metadata such as file-limit state is collected in scan_repo().
    """
    count = 0
    for path in sorted(repo_path.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        if count >= MAX_FILES:
            break
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        count += 1
        yield path, text


def _is_sensitive_secret_key(key: str) -> bool:
    key_norm = normalize_key(key)

    if not key_norm:
        return False
    if key_norm in SAFE_TOKEN_KEYS or key_norm.endswith("_tokens"):
        return False
    if "api_key" in key_norm or "apikey" in key_norm:
        return True
    if "private_key" in key_norm or "access_key" in key_norm:
        return True
    if "client_secret" in key_norm or "webhook_secret" in key_norm:
        return True
    if "secret" in key_norm:
        return True
    if "password" in key_norm or key_norm in {"passwd", "pwd"} or key_norm.endswith("_pwd"):
        return True
    if key_norm in SECRET_TOKEN_KEYS:
        return True
    if key_norm.endswith("_token") and key_norm not in SAFE_TOKEN_KEYS:
        # Token keys are only considered sensitive when qualified by an auth or
        # provider context, not when they are generic counters/parsers.
        qualifiers = (
            "auth", "access", "refresh", "bearer", "session", "csrf", "xsrf",
            "jwt", "github", "gh", "ghcr", "hf", "huggingface", "api", "client",
            "webhook", "slack", "discord", "openai", "anthropic", "gemini",
        )
        return any(part in key_norm for part in qualifiers)
    return False


def _strip_value(value: str) -> str:
    value = str(value).strip().rstrip(",)}]").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def _looks_like_placeholder_or_reference(value: str, line: str, rel: str = "") -> bool:
    stripped = _strip_value(value)
    normalized = stripped.lower()
    line_lower = line.lower()
    rel_lower = normalize_repo_path(rel).lower()

    if not stripped:
        return True
    if PLACEHOLDER_SECRET_RE.match(normalized):
        return True
    if any(token in line_lower for token in ("${", "${{", "secrets.", "os.environ", "getenv", "settings.", "env.")):
        return True
    if normalized in {"none", "null", "nil", "true", "false"}:
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", stripped):
        # Variable reference rather than an embedded secret value.
        return True
    if rel_lower.endswith(".env.example") or rel_lower.endswith(".env.sample"):
        return True
    if "/examples/" in rel_lower or "/example/" in rel_lower or "/tests/" in rel_lower or "/test/" in rel_lower:
        return True
    if Path(rel_lower).name.startswith("readme") and not _looks_like_high_entropy_secret(stripped):
        return True
    return False


def _looks_like_high_entropy_secret(value: str) -> bool:
    stripped = _strip_value(value)
    if len(stripped) < 16:
        return False
    if stripped.isdigit():
        return False
    has_alpha = bool(re.search(r"[A-Za-z]", stripped))
    has_digit_or_symbol = bool(re.search(r"[0-9._~+/=-]", stripped))
    return has_alpha and has_digit_or_symbol


def _iter_secret_assignments(line: str):
    # Prefer equals-style matches, then YAML/JSON colon-style matches.  Each
    # match is filtered by the key classifier, so normal token counters are safe.
    seen_spans: set[tuple[int, int]] = set()
    for regex in (KEY_VALUE_RE, KEY_COLON_RE):
        for match in regex.finditer(line):
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            key = match.group("key")
            if _is_sensitive_secret_key(key):
                yield key, match.group("value")


def mask_evidence(line: str) -> str:
    masked = line

    for key, value in _iter_secret_assignments(masked):
        if value:
            masked = masked.replace(value, "<MASKED>", 1)

    masked = AUTH_BEARER_RE.sub(r"\1<MASKED>", masked)
    return masked.strip()[:240]


def _secret_category_for_line(line: str, rel: str = "") -> str | None:
    if AUTH_BEARER_RE.search(line):
        return "secret_like_pattern_masked"

    categories: list[str] = []
    for _key, value in _iter_secret_assignments(line):
        if _looks_like_placeholder_or_reference(value, line, rel):
            categories.append("secret_placeholder_reference")
        elif _looks_like_high_entropy_secret(value):
            categories.append("secret_like_pattern_masked")
        else:
            categories.append("secret_placeholder_reference")

    if "secret_like_pattern_masked" in categories:
        return "secret_like_pattern_masked"
    if "secret_placeholder_reference" in categories:
        return "secret_placeholder_reference"
    return None


def _finding_from_line(category: str, rel: str, line_no: int, line: str) -> Finding:
    return Finding(
        category=category,
        severity=severity_for(category),
        file=normalize_repo_path(rel),
        line=line_no,
        evidence=mask_evidence(line),
        recommendation=recommendation_for(category),
    )


def _scan_file(rel: str, text: str) -> list[Finding]:
    findings: list[Finding] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        secret_category = _secret_category_for_line(line, rel)
        if secret_category is not None:
            findings.append(_finding_from_line(secret_category, rel, line_no, line))

        for category, patterns in RISK_PATTERNS.items():
            if any(re.search(pattern, line) for pattern in patterns):
                findings.append(_finding_from_line(category, rel, line_no, line))
                break

    return findings


def scan_repo(repo_url: str) -> dict:
    repo_path = clone_read_only(repo_url)
    findings: list[Finding] = []

    files_seen: list[str] = []
    files_skipped_large = 0
    files_unreadable = 0
    file_limit_reached = False

    try:
        for path in sorted(repo_path.rglob("*")):
            if ".git" in path.parts or not path.is_file():
                continue

            if len(files_seen) >= MAX_FILES:
                file_limit_reached = True
                break

            try:
                size = path.stat().st_size
            except OSError:
                files_unreadable += 1
                continue

            if size > MAX_FILE_BYTES:
                files_skipped_large += 1
                continue

            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                files_unreadable += 1
                continue

            rel = normalize_repo_path(path.relative_to(repo_path))
            files_seen.append(rel)
            findings.extend(_scan_file(rel, text))

        unique_findings = deduplicate_findings(findings)
        summary = summarize_findings(unique_findings)
        risk_score = compute_risk_score(unique_findings)
        repo_shape = summarize_repo_shape(files_seen)
        repo_shape["has_a2a_surface"] = any(item.category == "agent_surface_risk" for item in unique_findings)

        return {
            "repo_url": repo_url,
            "mode": "read_only_defensive",
            "classifier_version": CLASSIFIER_VERSION,
            "files_analyzed": len(files_seen),
            "findings": [f.__dict__ for f in unique_findings],
            "finding_summary": summary,
            "risk_score": risk_score,
            "risk_tier": risk_tier_for_score(risk_score),
            "review_load": review_load_for(unique_findings, len(files_seen), file_limit_reached),
            "repo_shape": repo_shape,
            "scan_limits": {
                "max_files": MAX_FILES,
                "max_file_bytes": MAX_FILE_BYTES,
                "file_limit_reached": file_limit_reached,
                "files_skipped_large": files_skipped_large,
                "files_unreadable": files_unreadable,
                "analysis_complete": not file_limit_reached,
            },
            "precision_notes": [
                "Generic token counters such as max_tokens, total_tokens, token_budget, pred_tokens, and life/info tokens are ignored as secrets.",
                "Secrets referenced through GitHub Actions secrets, env vars, .env.example files, and documentation examples are classified as low-severity placeholder references unless they look like hard-coded high-entropy values.",
                "A2A findings describe exposed protocol surface for defensive validation; they do not count as exploitation findings.",
            ],
        }
    finally:
        shutil.rmtree(repo_path.parent, ignore_errors=True)


def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str, int, str]] = set()
    unique: list[Finding] = []

    for finding in findings:
        key = (
            finding.category,
            finding.severity,
            normalize_repo_path(finding.file).lower(),
            finding.line,
            finding.evidence,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)

    return unique


def summarize_findings(findings: list[Finding]) -> dict:
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}

    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        by_category[finding.category] = by_category.get(finding.category, 0) + 1

    return {
        "total": len(findings),
        "by_severity": by_severity,
        "by_category": by_category,
    }


def severity_for(category: str) -> str:
    return {
        "ci_broad_permissions": "medium",
        "dangerous_shell_download": "high",
        "docker_privilege_risk": "medium",
        "secret_like_pattern_masked": "high",
        "secret_placeholder_reference": "low",
        "agent_surface_risk": "info",
    }.get(category, "low")


def recommendation_for(category: str) -> str:
    return {
        "ci_broad_permissions": "Reduce GitHub Actions permissions to least privilege.",
        "dangerous_shell_download": "Avoid piping remote scripts directly into a shell.",
        "docker_privilege_risk": "Avoid privileged containers, host networking, and Docker socket mounts unless strictly required and isolated.",
        "secret_like_pattern_masked": "Treat this as a possible exposed secret. Move secrets to a secret manager and rotate real values.",
        "secret_placeholder_reference": "Document placeholder values clearly and keep real secrets in a secret manager or runtime environment.",
        "agent_surface_risk": "Validate A2A message inputs, URLs, DataPart/FilePart content, and file sizes before processing.",
    }.get(category, "Review and document this behavior.")


def compute_risk_score(findings: list[Finding]) -> int:
    """Return a risk score without letting informational A2A surface saturate it."""
    unique = deduplicate_findings(findings)

    high = sum(1 for item in unique if item.severity == "high")
    medium = sum(1 for item in unique if item.severity == "medium")
    low = sum(1 for item in unique if item.severity == "low")
    info = sum(1 for item in unique if item.severity == "info")

    raw = high * 22 + medium * 11 + min(low, 12) * 2
    info_bonus = min(info, 8) + max(0, info - 8) // 20

    return min(raw + info_bonus, 100)


def risk_tier_for_score(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "elevated"
    if score >= 25:
        return "moderate"
    return "low"


def review_load_for(findings: list[Finding], files_analyzed: int, file_limit_reached: bool) -> dict:
    count = len(findings)
    if file_limit_reached or count >= 100 or files_analyzed >= MAX_FILES:
        level = "heavy"
    elif count >= 40:
        level = "medium"
    else:
        level = "light"

    return {
        "level": level,
        "finding_count": count,
        "files_analyzed": files_analyzed,
        "truncated": file_limit_reached,
    }


def summarize_repo_shape(files: list[str]) -> dict:
    normalized = [normalize_repo_path(item) for item in files]
    lower = [item.lower() for item in normalized]

    return {
        "has_dockerfile": any(
            item == "dockerfile"
            or item.endswith("/dockerfile")
            or item.startswith("dockerfile.")
            or "/dockerfile." in item
            for item in lower
        ),
        "has_github_actions": any(item.startswith(".github/workflows/") for item in lower),
        "has_tests": any(
            item.startswith("tests/")
            or item.startswith("test/")
            or "/tests/" in item
            or "/test/" in item
            or item.startswith("test_")
            or item.endswith("_test.py")
            for item in lower
        ),
        "has_pyproject": any(item == "pyproject.toml" or item.endswith("/pyproject.toml") for item in lower),
        "has_src": any(item.startswith("src/") or "/src/" in item for item in lower),
        "has_readme": any(Path(item).name.lower().startswith("readme") for item in normalized),
        "has_amber_manifest": any("amber" in item and item.endswith((".json5", ".json", ".toml")) for item in lower),
        # File-name heuristic only; scan_repo overwrites this from actual A2A findings.
        "has_a2a_surface": any("a2a" in item for item in lower),
    }
