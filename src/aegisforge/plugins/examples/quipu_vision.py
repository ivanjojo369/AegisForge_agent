"""AegisForge plugin bridge for Quipu Vision's audited visual workflow.

Destination:
    AegisForge_agent/src/aegisforge/plugins/examples/quipu_vision.py

Dependencies:
    Quipu-Vision/src/quipu_vision/{core,schemas,workflow,verifier}.py
    aegisforge.plugins.contracts.{PluginContext,PluginResult}

The plugin implements AegisForge's ``run(context) -> PluginResult`` protocol.
It is NOT an A2A/Executor auto-registration, a camera controller, a video
processor, an object classifier, an AWS deployment or a safety certificate.

Example metadata:
    {
      "operation": "verify",
      "reference_path": "samples/empty.png",
      "observed_path": "samples/changed.png",
      "followup_path": "samples/changed_again.png",
      "roi_xywh": [10, 20, 200, 120],
      "threshold": 32,
      "min_region_fraction": 0.004,
      "annotate": true
    }

Supported operations:
  inspect       - compare reference and observed images; optional follow-up
  verify        - same workflow, but require an actual follow-up image
  batch         - run up to 8 independent inspections; individual cases may
                  include followup_path to verify persistence
  capabilities  - describe available operations and their limitations

A2A DataPart input supports reference_image/observed_image/followup_image with
{mime_type, base64}, while A2A FilePart payloads are normalized by Executor.
Inline bytes are staged in an isolated, automatically deleted directory.
Input paths must be inside the configured input root; annotation targets must
be inside the configured output root. Both roots can be set via the constructor
or trusted QUIPU_VISION_INPUT_ROOT / QUIPU_VISION_OUTPUT_ROOT environment vars.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import ExitStack
import math
import tempfile
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..contracts import PluginContext, PluginResult


class _RequestError(ValueError):
    """Invalid input supplied by a plugin caller."""


class QuipuVisionPlugin:
    """Thin, bounded adapter to Quipu Vision's evidence-audited workflow.

    AegisForge currently has two plugin interfaces: a setup/teardown
    ``BasePlugin`` and a ``run`` protocol in ``plugins.contracts``. This class
    implements the latter, consistent with other ``plugins/examples``.
    """

    name = "quipu_vision"
    version = "0.4.0"
    supported_tracks = ["vision", "quipu_vision", "opencv"]
    MAX_BATCH = 8
    MAX_IMAGE_BYTES = 20 * 1024 * 1024
    MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
    MAX_INLINE_TOTAL_BYTES = 32 * 1024 * 1024
    INLINE_FIELDS = ("reference", "observed", "followup")
    MIME_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/bmp": ".bmp", "image/webp": ".webp"}
    _IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})
    _ANNOTATION_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})

    def __init__(
        self,
        *,
        input_root: str | Path | None = None,
        output_root: str | Path | None = None,
    ) -> None:
        # The environment is trusted deployment configuration, not caller data.
        configured_input = (
            input_root if input_root is not None
            else (os.getenv("QUIPU_VISION_INPUT_ROOT") or Path.cwd())
        )
        configured_output = (
            output_root if output_root is not None
            else (os.getenv("QUIPU_VISION_OUTPUT_ROOT") or Path.cwd() / "outputs" / "quipu_vision")
        )
        self._input_root = Path(configured_input).expanduser().resolve()
        self._output_root = Path(configured_output).expanduser().resolve()

    @classmethod
    def _error(cls, code: str, message: str, task_id: str = "") -> PluginResult:
        return PluginResult(
            ok=False,
            name=cls.name,
            output={
                "task_id": task_id,
                "capability": cls.name,
                "status": "error",
                "error": {"code": code, "message": message},
                "verified": False,
                "safety_certified": False,
            },
            warnings=[message],
        )

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _number(
        value: Any, *, key: str, integer: bool, minimum: float, maximum: float
    ) -> int | float:
        if type(value) not in (int, float):
            raise _RequestError(f"{key} must be a finite number")
        try:
            finite = math.isfinite(value)
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise _RequestError(f"{key} must be a finite number")
        if integer and not float(value).is_integer():
            raise _RequestError(f"{key} must be a whole number")
        number = int(value) if integer else float(value)
        if not minimum <= number <= maximum:
            raise _RequestError(f"{key} must be between {minimum} and {maximum}")
        return number

    @staticmethod
    def _boolean(value: Any, *, key: str) -> bool:
        if type(value) is not bool:
            raise _RequestError(f"{key} must be true or false")
        return value

    @classmethod
    def _decode_image(cls, raw: Any, *, key: str) -> tuple[bytes, str]:
        """Bounded inline image decoder; validates actual file signature and MIME."""
        if isinstance(raw, str):
            raw = {"base64": raw}
        if not isinstance(raw, Mapping):
            raise _RequestError(f"{key}_image must contain base64 image data")
        encoded = raw.get("base64")
        if encoded is None:
            encoded = raw.get("data_base64")
        if not isinstance(encoded, str) or not encoded:
            raise _RequestError(f"{key}_image.base64 must be a nonempty string")
        if len(encoded) > ((cls.MAX_INLINE_IMAGE_BYTES + 2) // 3) * 4 + 4:
            raise _RequestError(f"{key}_image exceeds 10 MiB")
        # Data URIs are intentionally unsupported: callers provide bare base64.
        try:
            binary = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise _RequestError(f"{key}_image contains invalid base64") from None
        if not binary or len(binary) > cls.MAX_INLINE_IMAGE_BYTES:
            raise _RequestError(f"{key}_image must be between 1 byte and 10 MiB")
        if binary.startswith(b"\x89PNG\r\n\x1a\n"):
            detected = "image/png"
        elif binary.startswith(b"\xff\xd8\xff"):
            detected = "image/jpeg"
        elif binary.startswith(b"BM"):
            detected = "image/bmp"
        elif binary.startswith(b"RIFF") and binary[8:12] == b"WEBP":
            detected = "image/webp"
        else:
            raise _RequestError(f"{key}_image is not a supported image format")
        declared = raw.get("mime_type", raw.get("mimeType"))
        if declared is not None and (not isinstance(declared, str) or declared.lower() != detected):
            raise _RequestError(f"{key}_image MIME type does not match image content")
        return binary, cls.MIME_SUFFIX[detected]

    def _stage_images(self, cases: list[Mapping[str, Any]], stage: Path) -> list[dict[str, Any]]:
        """Replace inline fields with private paths; caller deletes the stage."""
        staged: list[dict[str, Any]] = []
        total = 0
        for index, raw in enumerate(cases):
            case = dict(raw)
            for field in self.INLINE_FIELDS:
                input_key = f"{field}_image"
                path_key = f"{field}_path"
                if input_key not in case:
                    continue
                if case.get(path_key) is not None:
                    raise _RequestError(f"Choose either {path_key} or {input_key}, not both")
                binary, suffix = self._decode_image(case.pop(input_key), key=field)
                total += len(binary)
                if total > self.MAX_INLINE_TOTAL_BYTES:
                    raise _RequestError("Inline image payload exceeds 32 MiB total")
                target = stage / f"case_{index}_{field}{suffix}"
                target.write_bytes(binary)
                case[path_key] = target
            staged.append(case)
        return staged

    def _image_path(self, value: Any, *, key: str, staged_root: Path | None = None) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise _RequestError(f"{key} must be a nonempty image path")
        raw = Path(str(value)).expanduser()
        candidate = (raw if raw.is_absolute() else self._input_root / raw).resolve()
        if not (self._within(candidate, self._input_root) or
                (staged_root is not None and self._within(candidate, staged_root))):
            raise _RequestError(f"{key} must be inside the configured input root")
        if not candidate.is_file():
            raise _RequestError(f"{key} does not refer to an existing file")
        if candidate.suffix.lower() not in self._IMAGE_EXTENSIONS:
            raise _RequestError(f"{key} must be a PNG, JPEG, BMP or WebP image")
        if candidate.stat().st_size > self.MAX_IMAGE_BYTES:
            raise _RequestError(f"{key} exceeds the 20 MiB limit")
        return candidate

    def _annotation_path(self, value: Any, *, task_id: str, index: int) -> Path:
        if value is None:
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", task_id)[:48] or "task"
            return self._output_root / f"{safe_id}_{index}_{uuid.uuid4().hex[:12]}.png"
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise _RequestError("annotated_path must be a nonempty path")
        raw = Path(str(value)).expanduser()
        candidate = (raw if raw.is_absolute() else self._output_root / raw).resolve()
        if not self._within(candidate, self._output_root):
            raise _RequestError("annotated_path must be inside the configured output root")
        if candidate.suffix.lower() not in self._ANNOTATION_EXTENSIONS:
            raise _RequestError("annotated_path must end in .png, .jpg or .jpeg")
        if candidate.exists():
            raise _RequestError("annotated_path already exists; overwriting is disabled")
        return candidate

    def _parse_case(
        self,
        data: Mapping[str, Any],
        *,
        task_id: str,
        index: int,
        require_followup: bool,
        staged_root: Path | None = None,
    ) -> dict[str, Any]:
        reference = self._image_path(data.get("reference_path"), key="reference_path", staged_root=staged_root)
        observed = self._image_path(data.get("observed_path"), key="observed_path", staged_root=staged_root)
        if reference == observed:
            raise _RequestError("reference_path and observed_path must differ")
        followup_raw = data.get("followup_path")
        if require_followup and not followup_raw:
            raise _RequestError("operation=verify requires followup_path")
        followup = self._image_path(followup_raw, key="followup_path", staged_root=staged_root) if followup_raw else None
        if followup is not None and followup in (reference, observed):
            raise _RequestError("followup_path must differ from both initial images")

        roi = data.get("roi_xywh")
        if roi is not None:
            if (
                not isinstance(roi, (list, tuple))
                or len(roi) != 4
                or any(type(v) is not int for v in roi)
            ):
                raise _RequestError("roi_xywh must contain four integers [x,y,width,height]")
            if roi[0] < 0 or roi[1] < 0 or roi[2] <= 0 or roi[3] <= 0:
                raise _RequestError("roi_xywh must have nonnegative origin and positive size")
            roi = tuple(roi)

        threshold = self._number(data.get("threshold", 32), key="threshold", integer=True, minimum=1, maximum=255)
        region_fraction = self._number(
            data.get("min_region_fraction", 0.004),
            key="min_region_fraction", integer=False, minimum=0.000001, maximum=0.5,
        )
        annotate = self._boolean(data.get("annotate", False), key="annotate")
        annotation_raw = data.get("annotated_path")
        annotation = self._annotation_path(annotation_raw, task_id=task_id, index=index) if (
            annotate or annotation_raw is not None
        ) else None
        if annotation is not None and annotation in (reference, observed, followup):
            raise _RequestError("annotation output cannot overwrite an input image")

        return {
            "reference_path": reference,
            "observed_path": observed,
            "followup_path": followup,
            "roi": roi,
            "threshold": threshold,
            "min_region_fraction": region_fraction,
            "annotated_path": annotation,
        }

    @classmethod
    def _capabilities(cls, task_id: str) -> PluginResult:
        try:
            import cv2
            opencv_version = str(cv2.__version__)
        except ImportError:
            opencv_version = None
        return PluginResult(
            ok=True,
            name=cls.name,
            output={
                "task_id": task_id,
                "capability": cls.name,
                "version": cls.version,
                "operations": ["inspect", "verify", "batch", "capabilities"],
                "supported_inputs": ["PNG", "JPEG", "BMP", "WebP"],
                "input_modes": ["sandboxed_paths", "a2a_inline_base64", "a2a_filepart_bytes"],
                "max_inline_image_bytes": cls.MAX_INLINE_IMAGE_BYTES,
                "max_inline_total_bytes": cls.MAX_INLINE_TOTAL_BYTES,
                "temporary_uploads_removed": True,
                "batch_limit": cls.MAX_BATCH,
                "opencv_version": opencv_version,
                "opencv5_confirmed": bool(opencv_version and opencv_version.split(".", 1)[0] == "5"),
                "uses_audited_workflow": True,
                "followup_is_requested_not_captured": True,
                "supports_video": False,
                "supports_object_identification": False,
                "sandboxed_input_paths": True,
                "sandboxed_annotation_outputs": True,
                "verification_scope": "persistent_pixel_change_only",
                "safety_certified": False,
                "aws_deployed": False,
                "a2a_auto_registered": False,
            },
            warnings=[],
        )

    @staticmethod
    def _case_result(index: int, report: Any, *, followup_submitted: bool) -> dict[str, Any]:
        # Use the real contracts, not made-up dict keys or a repeat of core.inspect.
        from quipu_vision.schemas import VisionWorkflowResult
        if not isinstance(report, VisionWorkflowResult):
            raise RuntimeError("Quipu Vision returned an unexpected workflow result")
        evidence = report.to_dict()
        decision = report.decision
        return {
            "index": index,
            "status": decision.status,
            "next_action": decision.next_action,
            "summary": decision.reason,
            "change_detected": report.observation.change_detected,
            "change_pixel_fraction": report.observation.changed_pixel_fraction,
            "regions": [region.to_dict() for region in report.observation.regions],
            "regions_total": report.observation.regions_total,
            "followup_submitted": followup_submitted,
            "followup_processed": report.verification is not None,
            "verified": decision.confirmed_pixel_persistence,
            "verification_scope": "persistent_pixel_change_only",
            "human_review_required_for_safety": True,
            "safety_certified": False,
            "events": list(report.events),
            "evidence": evidence,
        }

    def run(self, context: PluginContext) -> PluginResult:
        task_id = str(getattr(context, "task_id", "") or "")[:128]
        metadata = getattr(context, "metadata", None) or {}
        if not isinstance(metadata, Mapping):
            return self._error("invalid_request", "metadata must be an object", task_id)
        operation = metadata.get("operation", "inspect")
        if not isinstance(operation, str):
            return self._error("invalid_request", "operation must be a string", task_id)
        operation = operation.strip().lower()
        if operation == "capabilities":
            return self._capabilities(task_id)
        if operation not in {"inspect", "verify", "batch"}:
            return self._error("invalid_request", "operation must be inspect, verify, batch or capabilities", task_id)

        started = time.perf_counter()
        try:
            if not self._input_root.is_dir() and not any(
                key in metadata for key in ("reference_image", "observed_image", "cases")
            ):
                raise _RequestError("configured input root does not exist")
            if operation == "batch":
                cases = metadata.get("cases")
                if not isinstance(cases, list) or not 1 <= len(cases) <= self.MAX_BATCH:
                    raise _RequestError(f"cases must have 1 to {self.MAX_BATCH} items")
                if any(not isinstance(case, Mapping) for case in cases):
                    raise _RequestError("every batch case must be an object")
            else:
                cases = [metadata]

            has_inline = any(any(f"{field}_image" in case for field in self.INLINE_FIELDS) for case in cases)
            with ExitStack() as stack:
                staged_root = None
                if has_inline:
                    self._output_root.mkdir(parents=True, exist_ok=True)
                    staged_root = Path(stack.enter_context(tempfile.TemporaryDirectory(
                        prefix="a2a-upload-", dir=str(self._output_root)
                    ))).resolve()
                    cases = self._stage_images(cases, staged_root)
                # Validate the entire batch before running any image analysis.
                validated = [
                    self._parse_case(case, task_id=task_id, index=index, require_followup=operation == "verify", staged_root=staged_root)
                    for index, case in enumerate(cases)
                ]
                annotations = [case["annotated_path"] for case in validated if case["annotated_path"] is not None]
                if len(annotations) != len(set(annotations)):
                    raise _RequestError("Batch cases must not share an annotation path")

                try:
                    from quipu_vision.workflow import run_visual_workflow
                except ImportError as exc:
                    raise RuntimeError("Quipu Vision engine is unavailable; install the Quipu-Vision package") from exc

                results = []
                warnings: list[str] = []
                for index, params in enumerate(validated):
                    report = run_visual_workflow(**params)
                    # Independently-built verification schemas are already audited
                    # inside run_visual_workflow; do not rerun core.inspect here.
                    results.append(self._case_result(index, report, followup_submitted=params["followup_path"] is not None))
                    warnings.extend(report.warnings)

                output: dict[str, Any] = {
                    "task_id": task_id,
                    "track": str(getattr(context, "track", "vision")),
                    "capability": self.name,
                    "plugin_version": self.version,
                    "operation": operation,
                    "status": "completed",
                    "processing_ms": round((time.perf_counter() - started) * 1000, 2),
                    "case_count": len(results),
                    "results": results,
                    "any_persistent_change": any(case["verified"] for case in results),
                    "any_followup_required": any(case["next_action"] == "request_second_observation" for case in results),
                    "safety_certified": False,
                }
                if len(results) == 1:
                    # Preserve keys consumed by the earlier Quipu Vision 0.2 bridge.
                    output.update({
                        "evidence": results[0]["evidence"],
                        "next_action": results[0]["next_action"],
                        "verified": results[0]["verified"],
                        "summary": results[0]["summary"],
                        "visual_status": results[0]["status"],
                    })
                return PluginResult(
                    ok=True,
                    name=self.name,
                    output=output,
                    warnings=list(dict.fromkeys(warnings)),
                )
        except _RequestError as exc:
            return self._error("invalid_request", str(exc), task_id)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            # Includes invalid image dimensions, malformed OpenCV report and
            # VisionSchemaError. Treat these as failure, never as verified data.
            return self._error(
                "visual_processing_failed",
                f"Visual inspection could not be validated ({type(exc).__name__}); check images, ROI and processing settings",
                task_id,
            )
        except RuntimeError as exc:
            if "engine is unavailable" in str(exc):
                return self._error("missing_engine", "Install the Quipu-Vision package in AegisForge's environment", task_id)
            return self._error("visual_processing_failed", "Visual processing failed; inspect local logs", task_id)
