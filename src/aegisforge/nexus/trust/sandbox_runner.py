"""Host-authorized, recipe-only Docker execution for AegisForge Nexus/Colossus.

Target: src/aegisforge/nexus/trust/sandbox_runner.py

SECURITY CONTRACT
-----------------
* This module defaults to NO execution backend. Importing, constructing and
  preparing a task do not start processes, write files, or contact providers.
* Only built-in recipes may be selected; no LLM-generated shell commands,
  custom argv, environment variables, Docker flags, images or mounts are used.
* A trusted host must provide an independently sanitized, separately located
  checkout, locally available images pinned to immutable sha256 digests, and a
  NexusWorkspacePolicy configured with a host-owned approval verifier.
* Both read permission and one-shot operation approval are required. Use a
  NexusApprovalGate whose trusted live binding provider independently derives
  runner.binding_for(the REAL pending plan) at the effect boundary. Showing a
  model response or possessing an approval ID is NOT sufficient authorization.
* The checkout is mounted READ ONLY, with no network, no extra capabilities,
  read-only root filesystem and resource limits. Docker is defense in depth,
  NOT a security boundary for adversarial hostile code. Do not give the
  container secrets, daemon socket access, or a private live checkout.
* The checkout is externally provisioned; this module NEVER copies source
  files or automatically applies patches. Output is reduced to hashes/counts,
  never raw stdout/stderr, credentials, file data or exception text.
* Path/permission/hash checks are vulnerable to concurrent host-side mutation
  (TOCTOU). Ensure checkout immutability and isolation in the trusted host.
  A dedicated VM/microVM is preferable for truly untrusted code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
from threading import Lock, Thread
from time import monotonic
from typing import Any, Mapping

from ..workspace.workspace_policy import NexusWorkspacePolicy
from .approval_gate import ActionBinding, canonical_sha256

SANDBOX_SCHEMA = "aegisforge.nexus.trust.sandbox.v1"
_HEX = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[a-f0-9]{32}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,200}@sha256:[a-f0-9]{64}$")
_EXEC = re.compile(r"^[A-Za-z0-9_.-]{1,50}$")
_PY_TEST = re.compile(r"(?:test_[^/]+|[^/]+_test)\.py$")
_JS_TEST = re.compile(r"(?:test_[^/]+|[^/]+\.(?:test|spec))\.(?:js|mjs|cjs)$")
_RECIPE_OP = {
    "python_ast": "execute_tool", "node_check": "execute_tool",
    "pytest_file": "run_tests", "node_test_file": "run_tests",
}
_RECIPE_EXT = {
    "python_ast": frozenset({".py"}),
    "node_check": frozenset({".js", ".mjs", ".cjs"}),
    "pytest_file": frozenset({".py"}),
    "node_test_file": frozenset({".js", ".mjs", ".cjs"}),
}
_BLOCK_DIR = frozenset({".git", ".ssh", ".aws", ".kube", ".gnupg", "secrets", "node_modules"})
_BLOCK_NAMES = frozenset({".env", "credentials.json", "secrets.json", "token.json", ".npmrc", ".pypirc"})
_BLOCK_SUFFIX = frozenset({".pem", ".key", ".p12", ".pfx", ".kdbx"})


class NexusSandboxError(ValueError):
    """The sandbox plan, approval, checkout or backend was rejected."""


def _relative_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise NexusSandboxError("target path must be a nonempty relative file path")
    value = raw.replace("\\", "/")
    if (value.startswith("/") or value.endswith("/") or "\x00" in value or ":" in value
            or any(p in ("", ".", "..") for p in value.split("/"))):
        raise NexusSandboxError("unsafe or noncanonical target path")
    parts = PurePosixPath(value).parts
    if (any(p.casefold() in _BLOCK_DIR for p in parts[:-1])
            or parts[-1].casefold() in _BLOCK_NAMES
            or parts[-1].casefold().startswith(".env.")
            or PurePosixPath(parts[-1].casefold()).suffix in _BLOCK_SUFFIX):
        raise NexusSandboxError("restricted file name or directory")
    return PurePosixPath(*parts).as_posix()


def _is_link(path: Path) -> bool:
    try:
        attr = path.lstat()
        return stat.S_ISLNK(attr.st_mode) or bool(
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            & getattr(attr, "st_file_attributes", 0)
        )
    except (OSError, ValueError, RuntimeError):
        return True


def _file(root: Path, relative_path: str, *, max_bytes: int) -> tuple[Path, str]:
    """Resolve an existing ordinary file with bounded complete SHA-256 read."""
    target = root
    for part in PurePosixPath(_relative_path(relative_path)).parts:
        target = target / part
        if _is_link(target):
            raise NexusSandboxError("linked or reparse-point path is prohibited")
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file() or resolved.stat().st_size > max_bytes:
            raise NexusSandboxError("file missing or larger than source limit")
        with resolved.open("rb") as fh:
            content = fh.read(max_bytes + 1)
        if len(content) > max_bytes or len(content) != resolved.stat().st_size:
            raise NexusSandboxError("file changed while reading or exceeds source limit")
        if b"\x00" in content:
            raise NexusSandboxError("binary files are not supported")
        return resolved, sha256(content).hexdigest()
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, NexusSandboxError):
            raise
        raise NexusSandboxError("source file cannot be securely inspected") from exc


def _digest(value: Mapping[str, Any]) -> str:
    return canonical_sha256(value)


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    timeout_seconds: int = 120
    max_source_bytes: int = 262_144
    max_output_bytes: int = 16_384
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 64

    def __post_init__(self) -> None:
        ranges = (
            ("timeout_seconds", 1, 600), ("max_source_bytes", 1024, 1_048_576),
            ("max_output_bytes", 1024, 262_144), ("memory_mb", 128, 8192),
            ("pids_limit", 16, 512),
        )
        for name, low, high in ranges:
            val = getattr(self, name)
            if type(val) is not int or not low <= val <= high:
                raise NexusSandboxError(f"{name} outside supported bounds")
        if (type(self.cpus) not in (int, float) or isinstance(self.cpus, bool)
                or not math.isfinite(float(self.cpus)) or not 0.1 <= self.cpus <= 8):
            raise NexusSandboxError("invalid CPU limit")

    def to_dict(self) -> dict[str, int | float]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_source_bytes": self.max_source_bytes,
            "max_output_bytes": self.max_output_bytes,
            "memory_mb": self.memory_mb,
            "cpus": float(self.cpus),
            "pids_limit": self.pids_limit,
        }


@dataclass(frozen=True, slots=True)
class SandboxPlan:
    mission_id: str
    plan_id: str
    recipe: str
    operation: str
    relative_path: str
    source_sha256: str
    backend_sha256: str
    limits: SandboxLimits
    plan_sha256: str
    _owner: object = field(compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SANDBOX_SCHEMA, "mission_id": self.mission_id,
            "plan_id": self.plan_id, "recipe": self.recipe,
            "operation": self.operation, "relative_path": self.relative_path,
            "source_sha256": self.source_sha256, "backend_sha256": self.backend_sha256,
            "limits": self.limits.to_dict(), "plan_sha256": self.plan_sha256,
            "approval_required": True, "executed": False,
        }


@dataclass(frozen=True, slots=True)
class SandboxOutcome:
    status: str  # passed | failed | timed_out | infra_error
    exit_code: int | None
    duration_ms: int
    output_sha256: str
    output_bytes: int
    output_truncated: bool

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "timed_out", "infra_error"}:
            raise NexusSandboxError("invalid backend result status")
        if self.exit_code is not None and (type(self.exit_code) is not int or abs(self.exit_code) > 100_000):
            raise NexusSandboxError("invalid exit code")
        if type(self.duration_ms) is not int or not 0 <= self.duration_ms <= 10_000_000:
            raise NexusSandboxError("invalid duration")
        if not isinstance(self.output_sha256, str) or not _HEX.fullmatch(self.output_sha256):
            raise NexusSandboxError("invalid output digest")
        if type(self.output_bytes) is not int or self.output_bytes < 0:
            raise NexusSandboxError("invalid output size")
        if type(self.output_truncated) is not bool:
            raise NexusSandboxError("invalid output truncation flag")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "exit_code": self.exit_code,
                "duration_ms": self.duration_ms, "output_sha256": self.output_sha256,
                "output_bytes": self.output_bytes, "output_truncated": self.output_truncated}


class NexusSandboxBackend(ABC):
    """Host-selected execution backend, never model-defined."""

    @abstractmethod
    def identity(self) -> Mapping[str, str]:
        """Stable, nonsecret backend identity; includes staged root and images."""

    @abstractmethod
    def preflight(self, live_root: Path, path: str, source_sha256: str, limits: SandboxLimits) -> None:
        """Verify separated sanitized checkout and exact authorized file bytes."""

    @abstractmethod
    def execute(self, recipe: str, path: str, limits: SandboxLimits) -> SandboxOutcome:
        """Run one fixed recipe in a constrained environment."""


class DockerSandboxBackend(NexusSandboxBackend):
    """No-network, read-only Docker backend for a host-sanitized checkout.

    Caller must independently prepare and lock down a sanitized checkout.
    Pinned local Docker images must be curated and include required tools.
    The Docker daemon and host environment remain TRUSTED components.
    """

    def __init__(
        self,
        sandbox_checkout: str | Path,
        *,
        python_image: str | None = None,
        node_image: str | None = None,
        docker_executable: str = "docker",
        max_checkout_entries: int = 12_000,
    ) -> None:
        root = Path(sandbox_checkout).expanduser()
        if not root.is_absolute() or _is_link(root) or not root.is_dir():
            raise NexusSandboxError("sandbox checkout must be an explicit existing directory")
        for name, image in (("python_image", python_image), ("node_image", node_image)):
            if image is not None and (not isinstance(image, str) or not _IMAGE.fullmatch(image)):
                raise NexusSandboxError(f"{name} must be a locally available digest-pinned image")
        if not python_image and not node_image:
            raise NexusSandboxError("at least one trusted digest-pinned image is required")
        if not isinstance(docker_executable, str) or not _EXEC.fullmatch(docker_executable):
            raise NexusSandboxError("docker executable must be a fixed command name")
        if type(max_checkout_entries) is not int or not 1 <= max_checkout_entries <= 200_000:
            raise NexusSandboxError("invalid checkout entry limit")
        canonical = root.resolve(strict=True)
        # Docker --mount uses commas as field separators, even without a shell.
        # Reject ambiguous mount sources rather than attempting to escape them.
        if "," in str(canonical) or any(ord(c) < 32 for c in str(canonical)):
            raise NexusSandboxError("checkout path cannot be encoded safely as a Docker mount")
        self.sandbox_checkout = canonical
        self.python_image = python_image
        self.node_image = node_image
        self.docker_executable = docker_executable
        self.max_checkout_entries = max_checkout_entries

    def identity(self) -> Mapping[str, str]:
        return {
            "type": "docker_digest_pinned_v1", "checkout": str(self.sandbox_checkout),
            "python_image": self.python_image or "", "node_image": self.node_image or "",
            "docker_executable": self.docker_executable,
        }

    def _validate_checkout(self) -> None:
        """Reject links, obvious secrets and oversized trees; not a secret scanner."""
        count = 0
        def fail_scan(_error: OSError) -> None:
            raise NexusSandboxError("cannot completely inspect sanitized checkout")

        for current, dirs, files in os.walk(self.sandbox_checkout, followlinks=False, onerror=fail_scan):
            here = Path(current)
            for name in dirs + files:
                count += 1
                if count > self.max_checkout_entries:
                    raise NexusSandboxError("sanitized checkout exceeds safe entry count")
                p = here / name
                low = name.casefold()
                if (_is_link(p) or low in _BLOCK_NAMES or low in _BLOCK_DIR
                        or low.startswith(".env.") or p.suffix.casefold() in _BLOCK_SUFFIX):
                    raise NexusSandboxError("checkout contains an unsafe path or link")
                # Hardlinks may alias data outside the sanitized checkout.
                try:
                    if p.is_file() and p.stat().st_nlink > 1:
                        raise NexusSandboxError("hardlinked checkout files are prohibited")
                except OSError as exc:
                    raise NexusSandboxError("cannot inspect staged file metadata") from exc

    def preflight(self, live_root: Path, path: str, source_sha256: str, limits: SandboxLimits) -> None:
        if not isinstance(source_sha256, str) or not _HEX.fullmatch(source_sha256):
            raise NexusSandboxError("invalid source digest")
        live = live_root.resolve(strict=True)
        staged = self.sandbox_checkout
        if staged == live or live in staged.parents or staged in live.parents:
            raise NexusSandboxError("checkout must not overlap the live workspace")
        self._validate_checkout()
        _, digest = _file(staged, path, max_bytes=limits.max_source_bytes)
        if digest != source_sha256:
            raise NexusSandboxError("staged source differs from approved workspace file")

    def _command(self, recipe: str, path: str) -> tuple[str, list[str]]:
        if recipe in {"python_ast", "pytest_file"}:
            image = self.python_image
        else:
            image = self.node_image
        if not image:
            raise NexusSandboxError("no trusted image configured for this recipe")
        if recipe == "python_ast":
            # Only parses syntax; does NOT import or execute inspected code.
            code = "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())"
            cmd = ["python", "-I", "-B", "-c", code, path]
        elif recipe == "node_check":
            cmd = ["node", "--check", path]
        elif recipe == "pytest_file":
            cmd = ["python", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   "-o", "addopts=", "--disable-warnings", "--", path]
        elif recipe == "node_test_file":
            cmd = ["node", "--test", path]
        else:
            raise NexusSandboxError("unsupported sandbox recipe")
        return image, cmd

    def execute(self, recipe: str, path: str, limits: SandboxLimits) -> SandboxOutcome:
        path = _relative_path(path)
        if recipe not in _RECIPE_OP:
            raise NexusSandboxError("unsupported recipe")
        image, command = self._command(recipe, path)
        name = "colossus-sandbox-" + secrets.token_hex(10)
        argv = [
            self.docker_executable, "run", "--rm", "--pull=never",
            "--name", name, "--network=none", "--ipc=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user=65534:65534", "--pids-limit", str(limits.pids_limit),
            "--memory", f"{limits.memory_mb}m", "--cpus", str(limits.cpus),
            "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=64m",
            "--mount", f"type=bind,src={self.sandbox_checkout},dst=/work,readonly",
            "--workdir", "/work", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTHONPATH=/work/src", "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            image, *command,
        ]
        started = monotonic()
        stream_digest = sha256()
        observed = 0
        captured = 0
        timeout = False
        proc: subprocess.Popen[bytes] | None = None
        reader: Thread | None = None

        def drain() -> None:
            nonlocal observed, captured
            assert proc is not None and proc.stdout is not None
            try:
                while True:
                    chunk = proc.stdout.read(8192)
                    if not chunk:
                        break
                    observed += len(chunk)
                    if captured < limits.max_output_bytes:
                        portion = chunk[:limits.max_output_bytes - captured]
                        stream_digest.update(portion)
                        captured += len(portion)
            except OSError:
                pass

        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, shell=False,
            )
            reader = Thread(target=drain, daemon=True, name="colossus-sandbox-output")
            reader.start()
            try:
                proc.wait(timeout=limits.timeout_seconds)
            except subprocess.TimeoutExpired:
                timeout = True
            if timeout:
                proc.kill()
            # Remove the container explicitly if Docker CLI timed out. Docker
            # --rm alone does not guarantee removal when the CLI is killed.
            if timeout:
                try:
                    subprocess.run([self.docker_executable, "rm", "-f", name],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=8, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if reader is not None:
                reader.join(timeout=5)
                if reader.is_alive():
                    # Never claim a reliable output digest if a child inherited
                    # the pipe and kept it open beyond the container's exit.
                    try:
                        if proc.stdout is not None:
                            proc.stdout.close()
                    except OSError:
                        pass
                    return SandboxOutcome("infra_error", None, int((monotonic()-started)*1000),
                                          sha256(b"").hexdigest(), 0, False)
            exit_code = proc.returncode
        except (OSError, ValueError, subprocess.SubprocessError):
            return SandboxOutcome("infra_error", None, int((monotonic()-started)*1000),
                                  sha256(b"").hexdigest(), 0, False)
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        elapsed = int((monotonic() - started) * 1000)
        # Docker's 125-127 codes report CLI/runtime failures; they are not
        # useful evidence that a test or syntax check failed.
        status = ("timed_out" if timeout else "infra_error" if exit_code in (125, 126, 127)
                  else "passed" if exit_code == 0 else "failed")
        return SandboxOutcome(status, exit_code, elapsed, stream_digest.hexdigest(),
                              observed, observed > limits.max_output_bytes)


class NexusSandboxRunner:
    """One-shot, policy-gated fixed-recipe sandbox coordinator."""

    def __init__(
        self,
        policy: NexusWorkspacePolicy,
        *,
        backend: NexusSandboxBackend | None = None,
        limits: SandboxLimits | None = None,
    ) -> None:
        if not isinstance(policy, NexusWorkspacePolicy):
            raise NexusSandboxError("policy must be NexusWorkspacePolicy")
        if backend is not None and not isinstance(backend, NexusSandboxBackend):
            raise NexusSandboxError("backend must be a trusted NexusSandboxBackend")
        if limits is not None and not isinstance(limits, SandboxLimits):
            raise NexusSandboxError("invalid sandbox limits")
        self.policy = policy
        self.backend = backend
        self.limits = limits or SandboxLimits()
        self._owner = object()
        self._used: set[str] = set()
        self._lock = Lock()

    def _backend_sha(self) -> str:
        if self.backend is None:
            return _digest({"disabled": True})
        identity = self.backend.identity()
        if (not isinstance(identity, Mapping) or not all(isinstance(k, str) and isinstance(v, str)
                                                        for k, v in identity.items())):
            raise NexusSandboxError("backend has invalid identity metadata")
        return _digest(dict(identity))

    def _source_sha(self, mission_id: str, path: str) -> str:
        self.policy.require(mission_id, "read_text", path)
        # The trusted host applies WorkspaceManager link/secret scope guards.
        src = self.policy.workspace._resolve(path, require_file=True)
        _, digest = _file(self.policy.workspace.root, path, max_bytes=self.limits.max_source_bytes)
        if src.suffix.casefold() not in {".py", ".js", ".mjs", ".cjs"}:
            raise NexusSandboxError("unsupported source type")
        return digest

    def _plan_data(self, plan: SandboxPlan) -> dict[str, Any]:
        return {
            "schema": SANDBOX_SCHEMA, "mission_id": plan.mission_id,
            "plan_id": plan.plan_id, "recipe": plan.recipe,
            "operation": plan.operation, "relative_path": plan.relative_path,
            "source_sha256": plan.source_sha256, "backend_sha256": plan.backend_sha256,
            "limits": plan.limits.to_dict(),
        }

    def prepare(self, mission_id: str, recipe: str, relative_path: str) -> SandboxPlan:
        """Create a review-only plan. Never invokes Docker or external code."""
        if not isinstance(recipe, str) or recipe not in _RECIPE_OP:
            raise NexusSandboxError("unsupported recipe")
        path = _relative_path(relative_path)
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in _RECIPE_EXT[recipe]:
            raise NexusSandboxError("recipe incompatible with target file")
        if recipe == "pytest_file" and not _PY_TEST.fullmatch(PurePosixPath(path).name):
            raise NexusSandboxError("pytest recipe requires a test file")
        if recipe == "node_test_file" and not _JS_TEST.fullmatch(PurePosixPath(path).name):
            raise NexusSandboxError("node-test recipe requires a test file")
        digest = self._source_sha(mission_id, path)
        plan = SandboxPlan(
            mission_id=mission_id, plan_id=secrets.token_hex(16), recipe=recipe,
            operation=_RECIPE_OP[recipe], relative_path=path, source_sha256=digest,
            backend_sha256=self._backend_sha(), limits=self.limits,
            plan_sha256="", _owner=self._owner,
        )
        object.__setattr__(plan, "plan_sha256", _digest(self._plan_data(plan)))
        return plan

    def _validate_live(self, plan: SandboxPlan) -> None:
        if not isinstance(plan, SandboxPlan) or plan._owner is not self._owner:
            raise NexusSandboxError("plan belongs to another sandbox runner")
        if not isinstance(plan.plan_id, str) or not _ID.fullmatch(plan.plan_id):
            raise NexusSandboxError("invalid plan ID")
        if plan.recipe not in _RECIPE_OP or plan.operation != _RECIPE_OP[plan.recipe]:
            raise NexusSandboxError("unsupported or modified plan recipe")
        if plan.limits != self.limits or plan.backend_sha256 != self._backend_sha():
            raise NexusSandboxError("sandbox configuration changed after plan preparation")
        if not isinstance(plan.plan_sha256, str) or _digest(self._plan_data(plan)) != plan.plan_sha256:
            raise NexusSandboxError("sandbox plan integrity mismatch")
        if plan.source_sha256 != self._source_sha(plan.mission_id, plan.relative_path):
            raise NexusSandboxError("live source changed after plan preparation")
        if self.backend is None:
            raise NexusSandboxError("sandbox execution backend is disabled")
        self.backend.preflight(self.policy.workspace.root, plan.relative_path,
                               plan.source_sha256, self.limits)

    def binding_for(self, plan: SandboxPlan) -> ActionBinding:
        """Recalculate full live approval binding for the trusted host.

        LiveBindingProvider should locate the actual current plan from its own
        pending-operations registry and call this method, never echo stored
        request hashes. This checks live source AND staged checkout state.
        """
        self._validate_live(plan)
        payload = {"recipe": plan.recipe, "operation": plan.operation,
                   "relative_path": plan.relative_path,
                   "source_sha256": plan.source_sha256, "limits": plan.limits.to_dict()}
        context = {"mission_id": plan.mission_id, "plan_id": plan.plan_id,
                   "plan_sha256": plan.plan_sha256, "backend_sha256": plan.backend_sha256,
                   "root": str(self.policy.workspace.root)}
        return ActionBinding(_digest(payload), _digest(context))

    def run(self, plan: SandboxPlan, *, action_id: str) -> dict[str, Any]:
        """Consume the reviewed plan, exact one-use approval, then run a recipe."""
        if not isinstance(action_id, str) or not _ID.fullmatch(action_id):
            raise NexusSandboxError("host-generated 32-hex action ID is required")
        # Atomic claim protects from repeated/parallel calls, including failures.
        if not isinstance(plan, SandboxPlan) or plan._owner is not self._owner:
            raise NexusSandboxError("foreign sandbox plan")
        with self._lock:
            if plan.plan_id in self._used:
                raise NexusSandboxError("sandbox plan already consumed")
            self._used.add(plan.plan_id)
        self._validate_live(plan)
        self.policy.require(plan.mission_id, plan.operation, plan.relative_path, action_id=action_id)
        # Recheck after approval immediately before beginning the host effect.
        self._validate_live(plan)
        assert self.backend is not None
        started = monotonic()
        try:
            outcome = self.backend.execute(plan.recipe, plan.relative_path, self.limits)
            if not isinstance(outcome, SandboxOutcome):
                raise NexusSandboxError("unsupported backend result")
        except Exception:
            outcome = SandboxOutcome("infra_error", None,
                                     min(int((monotonic() - started) * 1000), 10_000_000),
                                     sha256(b"").hexdigest(), 0, False)
        report = {
            "schema": SANDBOX_SCHEMA, "mission_id": plan.mission_id,
            "plan_id": plan.plan_id, "plan_sha256": plan.plan_sha256,
            "recipe": plan.recipe, "operation": plan.operation,
            "relative_path": plan.relative_path, "source_sha256": plan.source_sha256,
            "backend_sha256": plan.backend_sha256,
            "status": outcome.status, "result": outcome.to_dict(),
            "executed": True, "source_modified": False,
        }
        report["report_sha256"] = _digest(report)
        return report


__all__ = [
    "SANDBOX_SCHEMA", "NexusSandboxError", "SandboxLimits", "SandboxPlan",
    "SandboxOutcome", "NexusSandboxBackend", "DockerSandboxBackend",
    "NexusSandboxRunner",
]
