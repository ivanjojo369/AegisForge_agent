"""Operator-initiated, read-only Codex analysis on a small source snapshot.

Codex never receives Twitch data or operator credentials from this bridge. The
source checkout, studio state and Colossus checkpoints are never writable by it.
An analysis result is an untrusted recommendation, NOT a verified repair.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

TOPICS = {
    "adapter_contract": "Revisa los contratos de adaptadores existentes y propone una mejora concreta y compatible.",
    "mechanic_design": "Propón una nueva mecánica reutilizable basada en los patrones del catálogo, sin implementarla.",
    "quality_review": "Detecta riesgos de consistencia y compatibilidad del motor local y sugiere verificaciones acotadas.",
}
ACTIVITIES = ("gaming", "art", "coding", "science", "music", "sports", "talk")
SOURCE_FILES = (
    "activity_contracts.py", "activity_bridge.py", "creative_studio.py",
    "mechanic_registry.py", "mechanic_composer.py", "policy_engine.py",
)
MAX_SOURCE_BYTES = 180_000
MAX_RESULT_CHARS = 12_000
JOB_TIMEOUT_SECONDS = 120


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_env() -> dict[str, str]:
    """Keep Codex CLI path/login locations but remove inherited app secrets."""
    keep = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
            "HOME", "HOMEDRIVE", "HOMEPATH", "USERPROFILE", "APPDATA",
            "LOCALAPPDATA", "CODEX_HOME", "LANG", "LC_ALL", "COMSPEC"}
    return {k: v for k, v in os.environ.items() if k.upper() in keep}


class CodexEngineeringBridge:
    def __init__(self, source: Path):
        self._source = source
        self._lock = Lock()
        self._job: dict | None = None

    def status(self) -> dict:
        with self._lock:
            job = dict(self._job) if self._job is not None else None
        return {
            "cli_found": bool(shutil.which("codex")),
            "mode": "explicit_operator_read_only_snapshot",
            "changes_to_repository": False,
            "twitch_messages_used": False,
            "codex_invoked": bool(job and job.get("codex_invoked")),
            "topics": list(TOPICS),
            "activities": list(ACTIVITIES),
            "job": job,
        }

    def start(self, *, topic: str, activity: str) -> dict:
        if type(topic) is not str or topic not in TOPICS:
            raise ValueError("codex_topic_not_supported")
        if type(activity) is not str or activity not in ACTIVITIES:
            raise ValueError("codex_activity_not_supported")
        binary = shutil.which("codex")
        if not binary:
            raise ValueError("codex_cli_not_found")
        with self._lock:
            if self._job and self._job["status"] in ("queued", "running"):
                raise ValueError("codex_job_in_progress")
            job_id = "codex." + uuid4().hex
            self._job = {
                "job_id": job_id, "topic": topic, "activity": activity,
                "status": "queued", "requested_at_utc": _timestamp(),
                "codex_invoked": False, "result": None, "result_sha256": None,
                "error": None, "finished_at_utc": None,
            }
        Thread(target=self._run, args=(job_id, binary, topic, activity), daemon=True).start()
        return {"job_id": job_id, "status": "queued", "codex_invoked": False}

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            if self._job and self._job["job_id"] == job_id:
                self._job.update(values)

    def _run(self, job_id: str, binary: str, topic: str, activity: str) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="liveforge_codex_review_") as directory:
                folder = Path(directory)
                total = 0
                for name in SOURCE_FILES:
                    source = self._source / name
                    if source.is_symlink() or not source.is_file():
                        raise RuntimeError("source_snapshot_unavailable")
                    content = source.read_bytes()
                    total += len(content)
                    if total > MAX_SOURCE_BYTES:
                        raise RuntimeError("source_snapshot_too_large")
                    (folder / name).write_bytes(content)
                task = (
                    "Analiza SOLO los archivos Python de este directorio, una copia temporal "
                    "seleccionada de Live Forge. No leas otros directorios, no uses servicios externos, "
                    "no ejecutes codigo, no modifiques archivos y no propongas comandos de ejecucion. "
                    "Escribe en español una propuesta concreta, con secciones: Objetivo, Archivos "
                    "afectados, Contrato de entrada/salida, Permisos, Riesgos, Verificaciones. "
                    "No afirmes haber implementado ni verificado cambios. "
                    f"Actividad: {activity}. Tarea: {TOPICS[topic]}"
                )
                self._update(job_id, status="running", codex_invoked=True)
                completed = subprocess.run(
                    [binary, "exec", "--sandbox", "read-only", "--ephemeral",
                     "--ask-for-approval", "never", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-"],
                    input=task, capture_output=True, text=True, errors="replace",
                    cwd=str(folder), env=_safe_env(), timeout=JOB_TIMEOUT_SECONDS,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError("codex_cli_failed_check_login_or_version")
                result = completed.stdout.strip()[:MAX_RESULT_CHARS]
                if not result:
                    raise RuntimeError("codex_empty_response")
                self._update(job_id, status="completed", result=result,
                             result_sha256=hashlib.sha256(result.encode()).hexdigest(),
                             finished_at_utc=_timestamp())
        except subprocess.TimeoutExpired:
            self._update(job_id, status="failed", error="codex_execution_timed_out",
                         finished_at_utc=_timestamp())
        except Exception as exc:
            # Never expose path contents, CLI stderr, environment or tokens.
            reason = str(exc)
            allowed = {"source_snapshot_unavailable", "source_snapshot_too_large",
                       "codex_cli_failed_check_login_or_version", "codex_empty_response"}
            self._update(job_id, status="failed", error=reason if reason in allowed else "codex_execution_unavailable",
                         finished_at_utc=_timestamp())
