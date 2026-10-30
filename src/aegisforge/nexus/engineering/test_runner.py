"""Authorized, bounded test execution for AegisForge Nexus / Colossus.

Target: src/aegisforge/nexus/engineering/test_runner.py

Pipeline: NexusTestDiscovery.discover -> NexusTestRunner.prepare -> host review ->
NexusTestRunner.run (explicit one-use per-file approvals from NexusWorkspacePolicy).

NO code is executed by imports, construction, or prepare(). The runner defaults to
having NO execution backend. DockerSandboxBackend must be explicitly installed
by a trusted host and accepts a separate, preprovisioned *sanitized checkout*:
never mount the live workspace with private data into a test container.

Docker adds useful isolation controls but is NOT a boundary suitable for hostile
arbitrary code on its own; the Docker daemon, image, host configuration and
provisioning remain the operator's responsibility. This module does not run an
LLM or rewrite any repository file. Discovery rankings are hints, NOT approval.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
from threading import Lock, Thread
from time import monotonic
from typing import Any, Mapping, Sequence

from ..workspace.workspace_policy import NexusWorkspacePolicy
from .test_discovery import DISCOVERY_SCHEMA

RUNNER_SCHEMA = "aegisforge.nexus.engineering.test_runner.v1"
_SHA = re.compile(r"^[a-f0-9]{64}$")
_APPROVAL = re.compile(r"^[a-f0-9]{32}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,250}$")
_PY_TEST = re.compile(r"(?:test_[^/]+|[^/]+_test)\.py$")
_JS_TEST = re.compile(r"(?:test_[^/]+|[^/]+\.(?:test|spec))\.(?:js|mjs|cjs)$")


class NexusTestRunnerError(ValueError):
    """A test plan, trust boundary, permission or backend was rejected."""


def _path(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise NexusTestRunnerError("test path must be nonempty")
    text = raw.replace("\\", "/")
    if (text.startswith("/") or text.endswith("/") or ":" in text or "\x00" in text
            or any(segment in ("", ".", "..") for segment in text.split("/"))):
        raise NexusTestRunnerError("test path must be repository-relative without traversal")
    return PurePosixPath(text).as_posix()


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _within(path: str, scope: str) -> bool:
    return scope == "." or path == scope or path.startswith(scope + "/")


def _engine(path: str, framework: str) -> str:
    if framework == "python_static" and _PY_TEST.fullmatch(PurePosixPath(path).name):
        return "pytest"
    if framework == "js_ts_heuristic" and _JS_TEST.fullmatch(PurePosixPath(path).name):
        return "node_test"
    # TS, JSX, TSX and non-node JS harnesses need a separately reviewed backend.
    raise NexusTestRunnerError("test type not supported by the initial runner")


@dataclass(frozen=True, slots=True)
class TestRunLimits:
    max_test_files: int = 8
    timeout_seconds: int = 120  # per test file
    max_output_bytes: int = 16_384
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 64

    def __post_init__(self) -> None:
        fields = (
            ("max_test_files", 1, 32), ("timeout_seconds", 1, 900),
            ("max_output_bytes", 1024, 262_144), ("memory_mb", 128, 8192),
            ("pids_limit", 16, 512),
        )
        for name, low, high in fields:
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise NexusTestRunnerError(f"{name} outside supported limits")
        if (type(self.cpus) not in (float, int) or isinstance(self.cpus, bool)
                or not 0.1 <= self.cpus <= 8.0):
            raise NexusTestRunnerError("cpus outside supported limits")


@dataclass(frozen=True, slots=True)
class SelectedTest:
    relative_path: str
    source_sha256: str
    engine: str
    static_case_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "source_sha256": self.source_sha256,
            "engine": self.engine,
            "static_case_count": self.static_case_count,
        }


@dataclass(frozen=True, slots=True)
class TestRunPlan:
    mission_id: str
    plan_id: str
    catalog_sha256: str
    tests: tuple[SelectedTest, ...]
    plan_sha256: str
    _owner: object = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RUNNER_SCHEMA,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "catalog_sha256": self.catalog_sha256,
            "tests": [item.to_dict() for item in self.tests],
            "plan_sha256": self.plan_sha256,
            "executed": False,
            "approval_required": True,
        }


@dataclass(frozen=True, slots=True)
class BackendResult:
    status: str                 # passed | failed | timed_out | infra_error
    exit_code: int | None
    duration_ms: int
    output_sha256: str
    output_bytes: int
    output_truncated: bool

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "timed_out", "infra_error"}:
            raise NexusTestRunnerError("unsupported backend status")

    def to_dict(self) -> dict[str, Any]:
        # Deliberately do NOT put raw stdout/stderr, file contents or secrets
        # into an exported evidence record.
        return {
            "status": self.status, "exit_code": self.exit_code,
            "duration_ms": self.duration_ms, "output_sha256": self.output_sha256,
            "output_bytes": self.output_bytes,
            "output_truncated": self.output_truncated,
        }


class NexusTestBackend(ABC):
    """TRUSTED host-supplied execution boundary (never model supplied)."""

    @abstractmethod
    def preflight(self, source_root: Path, tests: tuple[SelectedTest, ...]) -> None:
        """Validate a separate sandbox checkout, including content hashes."""

    @abstractmethod
    def execute(self, test: SelectedTest, limits: TestRunLimits) -> BackendResult:
        """Execute a single fully authorized test file."""


class DockerSandboxBackend(NexusTestBackend):
    """Run tests in a separately provisioned Docker checkout, with no network.

    The images MUST exist locally (Docker --pull=never), be curated by the host,
    and contain the test's runtime/dependencies. It is not safe to run hostile
    code merely because it is in Docker. Never bind a live private workspace.
    """

    def __init__(
        self,
        sandbox_checkout: str | Path,
        *,
        python_image: str | None = None,
        node_image: str | None = None,
        docker_executable: str = "docker",
    ) -> None:
        sandbox = Path(sandbox_checkout).expanduser()
        if not sandbox.is_absolute() or not sandbox.is_dir() or sandbox.is_symlink():
            raise NexusTestRunnerError("sandbox_checkout must be an existing, explicit directory")
        self.sandbox_checkout = sandbox.resolve(strict=True)
        for name, image in (("python_image", python_image), ("node_image", node_image)):
            if image is not None and (not isinstance(image, str) or not _IMAGE.fullmatch(image)
                                      or ".." in image or "//" in image):
                raise NexusTestRunnerError(f"invalid {name}")
        if not python_image and not node_image:
            raise NexusTestRunnerError("configure at least one locally available trusted image")
        if not isinstance(docker_executable, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", docker_executable):
            raise NexusTestRunnerError("docker executable must be a fixed executable name")
        self.python_image = python_image
        self.node_image = node_image
        self.docker_executable = docker_executable

    def _test_file(self, test: SelectedTest) -> Path:
        relative = _path(test.relative_path)
        target = self.sandbox_checkout
        for component in PurePosixPath(relative).parts:
            target = target / component
            if target.is_symlink() or bool(getattr(target, "is_junction", lambda: False)()):
                raise NexusTestRunnerError("sandbox checkout includes a link/junction")
        resolved = target.resolve(strict=True)
        try:
            resolved.relative_to(self.sandbox_checkout)
        except ValueError as exc:
            raise NexusTestRunnerError("sandbox test path escaped checkout") from exc
        if not resolved.is_file() or resolved.stat().st_size > 262_144:
            raise NexusTestRunnerError("sandbox test file is missing or too large")
        if sha256(resolved.read_bytes()).hexdigest() != test.source_sha256:
            raise NexusTestRunnerError("sandbox test differs from authorized source")
        return resolved

    def preflight(self, source_root: Path, tests: tuple[SelectedTest, ...]) -> None:
        source = source_root.resolve(strict=True)
        staging = self.sandbox_checkout
        if staging == source or source in staging.parents or staging in source.parents:
            raise NexusTestRunnerError("sandbox checkout must be separate from the live workspace")
        for test in tests:
            if test.engine == "pytest" and self.python_image is None:
                raise NexusTestRunnerError("no trusted Python test image configured")
            if test.engine == "node_test" and self.node_image is None:
                raise NexusTestRunnerError("no trusted Node.js test image configured")
            self._test_file(test)

    def execute(self, test: SelectedTest, limits: TestRunLimits) -> BackendResult:
        self._test_file(test)  # validate immediately before invoking Docker
        image = self.python_image if test.engine == "pytest" else self.node_image
        if image is None:
            raise NexusTestRunnerError("no trusted image for test engine")
        container_name = "colossus-test-" + secrets.token_hex(10)
        runner = (
            ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
             "--disable-warnings", "--", test.relative_path]
            if test.engine == "pytest"
            else ["node", "--test", test.relative_path]
        )
        argv = [
            self.docker_executable, "run", "--rm", "--pull=never",
            "--name", container_name, "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user=65534:65534", "--pids-limit", str(limits.pids_limit),
            "--memory", f"{limits.memory_mb}m", "--cpus", str(limits.cpus),
            "--tmpfs", "/tmp:rw,nosuid,mode=1777,size=64m",
            "--mount", f"type=bind,src={self.sandbox_checkout},dst=/work,readonly",
            "--workdir", "/work", "--env", "PYTHONPATH=/work/src",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            image, *runner,
        ]
        started = monotonic()
        hasher = sha256()
        observed = 0
        captured = 0
        proc: subprocess.Popen[bytes] | None = None
        reader_thread: Thread | None = None
        timed_out = False
        infrastructure_error = False

        def drain() -> None:
            nonlocal observed, captured
            assert proc is not None and proc.stdout is not None
            # Drain the entire stream so a verbose test cannot deadlock on a
            # full pipe; keep constant memory and record only a bounded digest.
            try:
                while True:
                    chunk = proc.stdout.read(8192)
                    if not chunk:
                        break
                    observed += len(chunk)
                    if captured < limits.max_output_bytes:
                        allowed = chunk[:limits.max_output_bytes - captured]
                        hasher.update(allowed)
                        captured += len(allowed)
            except OSError:
                pass

        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, shell=False)
            reader_thread = Thread(target=drain, name="colossus-docker-output", daemon=True)
            reader_thread.start()
            try:
                proc.wait(timeout=limits.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            infrastructure_error = True
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.SubprocessError:
                    infrastructure_error = True
            if reader_thread is not None:
                reader_thread.join(timeout=3)
            # docker CLI may die while its container continues. Remove the
            # uniquely named container on every path, without a shell.
            try:
                subprocess.run(
                    [self.docker_executable, "rm", "-f", container_name],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        code = proc.returncode if proc is not None else None
        status = ("infra_error" if infrastructure_error or proc is None else
                  "timed_out" if timed_out else "infra_error" if code in (125, 126, 127)
                  else "passed" if code == 0 else "failed")
        return BackendResult(
            status=status, exit_code=code,
            duration_ms=int((monotonic() - started) * 1000),
            output_sha256=hasher.hexdigest(), output_bytes=observed,
            output_truncated=observed > limits.max_output_bytes,
        )


class NexusTestRunner:
    """Bind static discovery to one-shot, per-test approved sandbox operations."""

    def __init__(
        self,
        policy: NexusWorkspacePolicy,
        *,
        backend: NexusTestBackend | None = None,
        limits: TestRunLimits | None = None,
    ) -> None:
        if not isinstance(policy, NexusWorkspacePolicy):
            raise NexusTestRunnerError("policy must be a NexusWorkspacePolicy")
        if backend is not None and not isinstance(backend, NexusTestBackend):
            raise NexusTestRunnerError("backend must be an explicitly trusted NexusTestBackend")
        self.policy = policy
        self.backend = backend  # no backend => execution is impossible
        self.limits = limits or TestRunLimits()
        if not isinstance(self.limits, TestRunLimits):
            raise NexusTestRunnerError("limits must be TestRunLimits")
        self._owner = object()
        self._used: set[str] = set()
        self._lock = Lock()

    def prepare(
        self,
        mission_id: str,
        catalog: Mapping[str, Any],
        *,
        selected_paths: Sequence[str] | None = None,
        allow_partial_discovery: bool = False,
    ) -> TestRunPlan:
        """Build an inert test plan. No tool, network or process is started."""
        if not isinstance(catalog, Mapping) or catalog.get("schema") != DISCOVERY_SCHEMA:
            raise NexusTestRunnerError("unknown discovery catalog")
        if catalog.get("mission_id") != mission_id:
            raise NexusTestRunnerError("discovery belongs to a different mission")
        if catalog.get("status") not in {"discovered", "partial"}:
            raise NexusTestRunnerError("discovery has invalid status")
        if type(allow_partial_discovery) is not bool:
            raise NexusTestRunnerError("allow_partial_discovery must be boolean")
        if catalog["status"] == "partial" and not allow_partial_discovery:
            raise NexusTestRunnerError("partial discovery requires explicit host override")
        if catalog.get("executed") is not False or catalog.get("applied") is not False:
            raise NexusTestRunnerError("catalog must be purely static")
        digest = catalog.get("catalog_sha256")
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise NexusTestRunnerError("invalid catalog digest")
        raw_catalog = dict(catalog)
        del raw_catalog["catalog_sha256"]
        if _digest(raw_catalog) != digest:
            raise NexusTestRunnerError("catalog digest mismatch")
        scopes = catalog.get("test_scopes")
        rows = catalog.get("selected_tests")
        if not isinstance(scopes, list) or not scopes or not isinstance(rows, list):
            raise NexusTestRunnerError("discovery scope/test format invalid")
        validated_scopes = [_path(x) if x != "." else "." for x in scopes]
        if not rows:
            raise NexusTestRunnerError("discovery contains no selected test files")
        candidates: dict[str, SelectedTest] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise NexusTestRunnerError("invalid test row")
            path = _path(row.get("relative_path"))
            if path in candidates or not any(_within(path, scope) for scope in validated_scopes):
                raise NexusTestRunnerError("duplicate or out-of-scope test")
            hash_value = row.get("source_sha256")
            if not isinstance(hash_value, str) or not _SHA.fullmatch(hash_value):
                raise NexusTestRunnerError("test missing source hash")
            count = row.get("static_case_count")
            if type(count) is not int or not 0 <= count <= 2000:
                raise NexusTestRunnerError("invalid static test count")
            candidates[path] = SelectedTest(path, hash_value, _engine(path, row.get("framework")), count)
        if selected_paths is None:
            paths = tuple(candidates)
        else:
            if not isinstance(selected_paths, (list, tuple)) or isinstance(selected_paths, str):
                raise NexusTestRunnerError("selected_paths must be a sequence of paths")
            paths = tuple(_path(p) for p in selected_paths)
            if len(paths) != len(set(paths)) or any(p not in candidates for p in paths):
                raise NexusTestRunnerError("selected test outside discovered candidates")
        if not 1 <= len(paths) <= self.limits.max_test_files:
            raise NexusTestRunnerError("selected test count outside runner limits")
        tests = tuple(candidates[path] for path in paths)
        for test in tests:
            self._verify_source(mission_id, test)
        plan_id = secrets.token_hex(16)
        data = {
            "schema": RUNNER_SCHEMA, "mission_id": mission_id,
            "plan_id": plan_id, "catalog_sha256": digest,
            "tests": [t.to_dict() for t in tests],
        }
        return TestRunPlan(mission_id, plan_id, digest, tests, _digest(data), self._owner)

    def _verify_source(self, mission_id: str, test: SelectedTest) -> None:
        # Uses the policy facade; no direct workspace/backend access.
        content = self.policy.read_text(mission_id, test.relative_path)
        if sha256(content.encode("utf-8")).hexdigest() != test.source_sha256:
            raise NexusTestRunnerError("test changed since discovery; prepare a fresh catalog")

    def run(
        self,
        plan: TestRunPlan,
        *,
        approvals: Mapping[str, str],
        stop_on_failure: bool = True,
    ) -> dict[str, Any]:
        """Execute only with a trusted backend and single-use exact approvals.

        Each test file needs a distinct 32-digit hex action ID, previously
        approved in an independent trusted-host store for mission/path/run_tests.
        Approval IDs are never accepted as signatures or self-authorization.
        """
        if not isinstance(plan, TestRunPlan) or plan._owner is not self._owner:
            raise NexusTestRunnerError("plan belongs to a different test runner")
        if type(stop_on_failure) is not bool:
            raise NexusTestRunnerError("stop_on_failure must be boolean")
        data = {
            "schema": RUNNER_SCHEMA, "mission_id": plan.mission_id,
            "plan_id": plan.plan_id, "catalog_sha256": plan.catalog_sha256,
            "tests": [t.to_dict() for t in plan.tests],
        }
        if _digest(data) != plan.plan_sha256:
            raise NexusTestRunnerError("plan tampered")
        if self.backend is None:
            raise NexusTestRunnerError("no isolated execution backend configured")
        if not isinstance(approvals, Mapping) or set(approvals) != {t.relative_path for t in plan.tests}:
            raise NexusTestRunnerError("one approval is required for every selected test")
        ids = list(approvals.values())
        if any(not isinstance(x, str) or not _APPROVAL.fullmatch(x) for x in ids) or len(set(ids)) != len(ids):
            raise NexusTestRunnerError("approval IDs must be unique, host-generated 32-digit hex IDs")
        # Claim BEFORE approval callbacks or sandbox side effects; never replay.
        with self._lock:
            if plan.plan_id in self._used:
                raise NexusTestRunnerError("test run plan already consumed")
            self._used.add(plan.plan_id)
        for test in plan.tests:
            self._verify_source(plan.mission_id, test)
        self.backend.preflight(self.policy.workspace.root, plan.tests)
        for test in plan.tests:
            self.policy.require(plan.mission_id, "run_tests", test.relative_path,
                                action_id=approvals[test.relative_path])
        outcomes: list[dict[str, Any]] = []
        for test in plan.tests:
            # Avoid continuing after the test file changed before execution.
            self._verify_source(plan.mission_id, test)
            try:
                result = self.backend.execute(test, self.limits)
                if not isinstance(result, BackendResult):
                    raise NexusTestRunnerError("backend returned unsupported result")
            except Exception:
                result = BackendResult("infra_error", None, 0, sha256(b"").hexdigest(), 0, False)
            outcomes.append({"relative_path": test.relative_path,
                             "source_sha256": test.source_sha256,
                             **result.to_dict()})
            if stop_on_failure and result.status != "passed":
                break
        passed = sum(item["status"] == "passed" for item in outcomes)
        failed = sum(item["status"] == "failed" for item in outcomes)
        other = len(outcomes) - passed - failed
        status = ("passed" if passed == len(plan.tests) else
                  "failed" if failed else "inconclusive")
        report = {
            "schema": RUNNER_SCHEMA, "mission_id": plan.mission_id,
            "plan_id": plan.plan_id, "plan_sha256": plan.plan_sha256,
            "catalog_sha256": plan.catalog_sha256,
            "status": status, "attempted": len(outcomes),
            "selected": len(plan.tests), "passed": passed, "failed": failed,
            "inconclusive": other, "results": outcomes,
            "executed": bool(outcomes), "source_modified": False,
            "warnings": (["Execution stopped before all selected tests completed"]
                         if len(outcomes) != len(plan.tests) else []),
        }
        report["report_sha256"] = _digest(report)
        return report


__all__ = [
    "RUNNER_SCHEMA", "NexusTestRunnerError", "TestRunLimits", "SelectedTest",
    "TestRunPlan", "BackendResult", "NexusTestBackend", "DockerSandboxBackend",
    "NexusTestRunner",
]
