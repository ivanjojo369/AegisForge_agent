"""Conservative secret screening for AegisForge Nexus / Colossus.

Target: src/aegisforge/nexus/trust/secret_guard.py

This is a bounded, deterministic DATA-LOSS-PREVENTION (DLP) helper, NOT a
security boundary or a substitute for credential management. It never sends
requests, executes commands, writes files or reads environment variables.

Use ``require_clean_text`` before exposing text to a model or evidence system;
use ``sanitize_text``/``sanitize_payload`` when an operator explicitly chooses
redaction. A successful scan means only that supported patterns were not found.
Unknown, encrypted, split, custom-format and encoded secrets may be missed.

A trusted host MUST separately enforce workspace permissions, secret-free
build contexts, isolation and approval. Do not log unsanitized exceptions or
serialize the raw source when recording scan outcomes. Avoid exposing
``redacted_text`` to an untrusted consumer without a separate privacy review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import math
import os
import re
import stat
from typing import Any, Mapping

SECRET_GUARD_SCHEMA = "aegisforge.nexus.trust.secret_guard.v1"
REDACTION_MARKER = "[REDACTED:SECRET]"


class NexusSecretGuardError(ValueError):
    """Rejected/unsafe input. Error messages intentionally contain no input."""


@dataclass(frozen=True, slots=True)
class SecretGuardLimits:
    max_text_chars: int = 131_072
    max_file_bytes: int = 262_144
    max_findings: int = 256
    max_payload_nodes: int = 4096
    max_payload_depth: int = 16

    def __post_init__(self) -> None:
        limits = (
            ("max_text_chars", 1024, 1_048_576),
            ("max_file_bytes", 1024, 2_097_152),
            ("max_findings", 1, 2048),
            ("max_payload_nodes", 1, 100_000),
            ("max_payload_depth", 1, 64),
        )
        for key, lo, hi in limits:
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise NexusSecretGuardError(f"invalid guard limit: {key}")


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """Nonsecret finding metadata. Never includes the matched credential."""

    rule: str
    start: int
    end: int
    line: int
    column: int

    def to_dict(self) -> dict[str, str | int]:
        return {"rule": self.rule, "start": self.start, "end": self.end,
                "line": self.line, "column": self.column}


@dataclass(frozen=True, slots=True)
class SecretScan:
    """Metadata-only result: safe=False means do not disclose unmodified input."""

    findings: tuple[SecretFinding, ...]
    inspected_characters: int
    schema: str = SECRET_GUARD_SCHEMA

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "clean": self.clean,
                "findings_count": len(self.findings),
                "inspected_characters": self.inspected_characters,
                "findings": [f.to_dict() for f in self.findings]}


@dataclass(frozen=True, slots=True)
class SanitizedText:
    """A redacted string. The content is intentionally excluded from repr()."""

    text: str = field(repr=False)
    scan: SecretScan

    def to_summary(self) -> dict[str, Any]:
        return {"schema": SECRET_GUARD_SCHEMA, "findings_count": len(self.scan.findings),
                "redacted": not self.scan.clean}


@dataclass(frozen=True, slots=True)
class SanitizedPayload:
    """Redacted JSON-compatible value; never include it in generic logs."""

    value: Any = field(repr=False)
    redactions: int
    schema: str = SECRET_GUARD_SCHEMA

    def to_summary(self) -> dict[str, Any]:
        return {"schema": self.schema, "redactions": self.redactions}


# Patterns are heuristic indicators. All matching is bounded by max_text_chars.
# Each pattern captures ONLY the substring to replace, not surrounding text.
_VENDOR_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|glpat-[A-Za-z0-9_-]{15,}"
    r"|xox[baprs]-[A-Za-z0-9-]{15,}"
    r"|sk_live_[A-Za-z0-9]{16,}"
    r"|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"
    r")(?![A-Za-z0-9_])"
)
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)[ \t]+(?P<value>[A-Za-z0-9._~+/-]{8,}={0,2})")
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")
_URL_USERINFO = re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?|ftp)://(?P<value>[^\s/@:\\]{1,128}:[^\s/@\\]{1,256})@")
_FIELD_NAME = (
    r"(?:oauthClientSecret|clientSecret|apiKey|accessToken|refreshToken|privateKey|sessionToken|"
    r"NEBIUS_API_KEY|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|"
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GITLAB_TOKEN|"
    r"TWITCH_CLIENT_SECRET|DATABASE_URL|CONNECTION_STRING|"
    r"api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|private[_-]?key|secret[_-]?key|"
    r"password|passwd|authorization|auth[_-]?token|"
    r"credentials?|bearer[_-]?token)"
)
# Quoted values can contain spaces. Backslash-escaped quotes are handled
# conservatively; complex string encodings must be checked by the host.
_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w-])(?:['\"]?" + _FIELD_NAME + r"['\"]?)\s*[:=]\s*"
    r"(?:\"(?P<double>(?:\\.|[^\"\\\r\n]){1,4096})\"|"
    r"'(?P<single>(?:\\.|[^'\\\r\n]){1,4096})'|"
    r"(?P<bare>[^\s,;\]}\r\n]{3,4096}))"
)
_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN (?:[A-Z0-9 ]*\s)?PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END (?:[A-Z0-9 ]*\s)?PRIVATE KEY-----")
_SENSITIVE_FIELDS = frozenset({
    "api_key", "apikey", "api_token", "access_token", "refresh_token",
    "auth_token", "bearer_token", "authorization", "password", "passwd",
    "secret", "secret_key", "private_key", "client_secret", "client_password",
    "aws_secret_access_key", "aws_session_token", "nebius_api_key",
    "openai_api_key", "anthropic_api_key", "github_token", "gitlab_token",
    "twitch_client_secret", "credentials", "credential", "database_url",
    "connection_string", "access_key", "webhook_secret", "signing_key",
})
_BLOCKED_DIRS = frozenset({
    ".git", ".ssh", ".aws", ".kube", ".gnupg", "secrets", ".venv",
    "venv", "node_modules", "__pycache__",
})
_BLOCKED_NAMES = frozenset({
    "id_rsa", "id_ed25519", ".npmrc", ".pypirc", "credentials.json",
    "credentials.yaml", "credentials.yml", "secrets.json", "secrets.yaml",
    "secrets.yml", "token.json", "tokens.json", "terraform.tfstate",
})
_BLOCKED_EXT = frozenset({".pem", ".key", ".pfx", ".p12", ".kdbx"})
_DRIVE_PREFIX = re.compile(r"^[a-zA-Z]:")


def _sensitive_key(key: str) -> bool:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = snake.casefold().replace("-", "_").replace(".", "_")
    return (normalized in _SENSITIVE_FIELDS
            or normalized.endswith(("_api_key", "_secret", "_password", "_token", "_credential")))


def _is_link(path: Path) -> bool:
    try:
        attrs = path.lstat()
        return stat.S_ISLNK(attrs.st_mode) or bool(
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            & getattr(attrs, "st_file_attributes", 0)
        )
    except (OSError, RuntimeError, ValueError):
        return True


class NexusSecretGuard:
    """Check/sanitize small text and JSON payloads before external disclosure.

    Defaults: BLOCK detected secrets; explicit redaction is opt-in. All public
    error strings are generic to prevent reproducing secrets in exception logs.
    Never regard this scanner alone as sufficient for sanitizing a whole repo.
    """

    def __init__(self, limits: SecretGuardLimits | None = None) -> None:
        if limits is not None and not isinstance(limits, SecretGuardLimits):
            raise NexusSecretGuardError("limits must be SecretGuardLimits")
        self.limits = limits or SecretGuardLimits()

    def _validate_text(self, text: str) -> None:
        if not isinstance(text, str):
            raise NexusSecretGuardError("expected a string")
        if len(text) > self.limits.max_text_chars:
            raise NexusSecretGuardError("text exceeds secret scanning limit")
        if "\x00" in text:
            raise NexusSecretGuardError("binary/control content is not supported")

    def _spans(self, text: str) -> list[tuple[int, int, str]]:
        """Return bounded candidate spans with nonsecret rule labels."""
        spans: list[tuple[int, int, str]] = []

        def record(start: int, end: int, rule: str) -> None:
            if end <= start:
                return
            if len(spans) >= self.limits.max_findings:
                raise NexusSecretGuardError("too many potential secrets; disclosure denied")
            spans.append((start, end, rule))

        for match in _VENDOR_TOKEN.finditer(text):
            record(*match.span(), "vendor_token")
        for match in _BEARER.finditer(text):
            record(*match.span("value"), "authorization_header")
        for match in _JWT.finditer(text):
            record(*match.span(), "jwt")
        for match in _URL_USERINFO.finditer(text):
            record(*match.span("value"), "url_credentials")
        for match in _ASSIGNMENT.finditer(text):
            for group in ("double", "single", "bare"):
                candidate = match.group(group)
                if candidate is not None:
                    # Source code declarations/computed expressions are NOT
                    # embedded credentials. Avoid flagging `api_key: str` or
                    # `api_key = os.getenv(...)` as literal secret material.
                    if group == "bare" and (
                        candidate.casefold() in {
                            "str", "int", "bool", "float", "bytes", "none",
                            "null", "true", "false", "optional", "mapping",
                            "bearer", "basic", "field", "secretstr",
                        }
                        or bool(re.match(r"(?:field|getattr|dict|os\.[\w.]+|self\.[\w.]+|config\.[\w.]+)\(", candidate))
                        or candidate.startswith(("self.", "os.", "config."))
                    ):
                        break
                    record(*match.span(group), "credential_assignment")
                    break
        for match in _PRIVATE_KEY_BEGIN.finditer(text):
            ending = _PRIVATE_KEY_END.search(text, match.end())
            # Malformed/unclosed PEM: mask everything from the header onward.
            end = ending.end() if ending else len(text)
            record(match.start(), end, "private_key_block")
        return sorted(spans, key=lambda x: (x[0], -x[1], x[2]))

    def scan_text(self, text: str) -> SecretScan:
        """Return metadata ONLY; no raw matched credential is returned."""
        self._validate_text(text)
        results: list[SecretFinding] = []
        for start, end, rule in self._spans(text):
            line = text.count("\n", 0, start) + 1
            last = text.rfind("\n", 0, start)
            results.append(SecretFinding(rule, start, end, line, start - last))
        return SecretScan(tuple(results), len(text))

    def require_clean_text(self, text: str) -> str:
        """Fail closed if supported secret indicators are detected."""
        if not self.scan_text(text).clean:
            raise NexusSecretGuardError("possible secret detected; disclosure denied")
        return text

    def sanitize_text(self, text: str, *, mode: str = "block") -> SanitizedText:
        """Block by default, or explicitly redact matched spans in a copy."""
        if mode not in ("block", "redact"):
            raise NexusSecretGuardError("invalid sanitization mode")
        scan = self.scan_text(text)
        if not scan.clean and mode == "block":
            raise NexusSecretGuardError("possible secret detected; disclosure denied")
        if scan.clean:
            return SanitizedText(text, scan)
        merged: list[list[int]] = []
        for finding in scan.findings:
            if merged and finding.start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], finding.end)
            else:
                merged.append([finding.start, finding.end])
        parts: list[str] = []
        position = 0
        for start, end in merged:
            parts.extend((text[position:start], REDACTION_MARKER))
            position = end
        parts.append(text[position:])
        return SanitizedText("".join(parts), scan)

    def sanitize_payload(self, payload: Any, *, mode: str = "block") -> SanitizedPayload:
        """Screen/redact JSON-like data, including values under sensitive keys.

        Non-string keys, objects, bytes, nonfinite numbers and excessive nesting
        are rejected. In redaction mode, sensitive-key values are replaced in
        full; other strings are scanned as text. No mutation of the input.
        """
        if mode not in ("block", "redact"):
            raise NexusSecretGuardError("invalid sanitization mode")
        nodes = 0
        redactions = 0
        seen: set[int] = set()

        def replace(value: Any, depth: int) -> Any:
            nonlocal nodes, redactions
            nodes += 1
            if nodes > self.limits.max_payload_nodes or depth > self.limits.max_payload_depth:
                raise NexusSecretGuardError("payload exceeds scanning limits")
            if value is None or type(value) in (bool, int):
                return value
            if type(value) is float:
                if not math.isfinite(value):
                    raise NexusSecretGuardError("nonfinite payload value")
                return value
            if isinstance(value, str):
                screened = self.sanitize_text(value, mode=mode)
                redactions += len(screened.scan.findings)
                return screened.text
            if isinstance(value, (list, tuple, Mapping)):
                key = id(value)
                if key in seen:
                    raise NexusSecretGuardError("cyclic or repeated payload container")
                seen.add(key)
                try:
                    if isinstance(value, Mapping):
                        result: dict[str, Any] = {}
                        for field_name, child in value.items():
                            if type(field_name) is not str:
                                raise NexusSecretGuardError("payload field names must be strings")
                            self.require_clean_text(field_name)
                            if _sensitive_key(field_name):
                                if mode == "block":
                                    raise NexusSecretGuardError("sensitive payload field; disclosure denied")
                                redactions += 1
                                result[field_name] = REDACTION_MARKER
                            else:
                                result[field_name] = replace(child, depth + 1)
                        return result
                    return [replace(child, depth + 1) for child in value]
                finally:
                    seen.remove(key)
            raise NexusSecretGuardError("unsupported payload type")

        result = replace(payload, 0)
        return SanitizedPayload(result, redactions)

    def require_clean_payload(self, payload: Any) -> Any:
        return self.sanitize_payload(payload, mode="block").value

    def scan_file(self, root: str | Path, relative_path: str) -> SecretScan:
        """Read a bounded regular UTF-8 file from a trusted root, WITHOUT writes.

        Strict relative paths, no symlinks/junctions. Sensitive file names are
        denied rather than read. This is not a race-free sandbox: trusted hosts
        must prevent concurrent adversarial modification of the source tree.
        """
        if not isinstance(root, (str, Path)) or not str(root).strip():
            raise NexusSecretGuardError("trusted absolute root required")
        base = Path(root)
        if not base.is_absolute() or _is_link(base):
            raise NexusSecretGuardError("unsafe file root")
        try:
            base = base.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise NexusSecretGuardError("file root unavailable") from exc
        if not base.is_dir():
            raise NexusSecretGuardError("file root must be directory")
        if not isinstance(relative_path, str) or not relative_path:
            raise NexusSecretGuardError("relative path required")
        raw = relative_path.replace("\\", "/")
        if (raw.startswith("/") or _DRIVE_PREFIX.match(raw) or "\x00" in raw
                or any(p in ("", ".", "..") or ":" in p for p in raw.split("/"))):
            raise NexusSecretGuardError("unsafe relative path")
        parts = PurePosixPath(raw).parts
        filename = parts[-1].casefold()
        if (any(part.casefold() in _BLOCKED_DIRS for part in parts[:-1])
                or filename in _BLOCKED_NAMES or filename == ".env"
                or filename.startswith(".env.") or filename.endswith(".env")
                or PurePosixPath(filename).suffix in _BLOCKED_EXT):
            raise NexusSecretGuardError("restricted file; disclosure denied")
        target = base
        for part in parts:
            target = target / part
            if _is_link(target):
                raise NexusSecretGuardError("linked file or directory rejected")
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(base)
            if not resolved.is_file():
                raise NexusSecretGuardError("regular file required")
            size = resolved.stat().st_size
            if size > self.limits.max_file_bytes:
                raise NexusSecretGuardError("file exceeds secret scanning limit")
            with resolved.open("rb") as fh:
                content = fh.read(self.limits.max_file_bytes + 1)
            if len(content) != size or len(content) > self.limits.max_file_bytes:
                raise NexusSecretGuardError("file changed during inspection")
            text = content.decode("utf-8-sig", errors="strict")
        except (OSError, UnicodeError, ValueError) as exc:
            if isinstance(exc, NexusSecretGuardError):
                raise
            raise NexusSecretGuardError("cannot safely inspect file") from None
        return self.scan_text(text)


__all__ = [
    "SECRET_GUARD_SCHEMA", "REDACTION_MARKER", "NexusSecretGuardError",
    "SecretGuardLimits", "SecretFinding", "SecretScan", "SanitizedText",
    "SanitizedPayload", "NexusSecretGuard",
]
