r"""Regression tests for Quipu Vision's OpenCV image-change workflow.

Place this file in: AegisForge_agent/Quipu-Vision/tests/test_workflow.py

Run from AegisForge_agent with the environment where Quipu Vision is installed:
    .\.venv\Scripts\python.exe -m unittest discover -s .\Quipu-Vision\tests -p test_workflow.py -v

No live camera, A2A server, AWS credentials, network access, external fixtures,
or pytest installation is required. Input PNGs are generated inside a temporary
folder and deleted automatically. Tests prove pixel-change processing, NOT
object identification, model accuracy on real-world data or physical safety.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np

from quipu_vision.core import inspect
from quipu_vision.schemas import VisionSchemaError, VisionWorkflowResult
from quipu_vision.verifier import (
    audit_engine_report,
    audit_followup_pair,
    choose_next_action,
)
from quipu_vision.workflow import run_visual_workflow


class TestVisualWorkflow(unittest.TestCase):
    """Test the production engine, not an invented/simulated image classifier."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="quipu_vision_test_")
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name)

        # High-contrast, fixed-camera scenes give deterministic contours on both
        # OpenCV 4 and 5. Real-camera robustness must be evaluated separately.
        self.baseline = np.full((192, 256, 3), 20, dtype=np.uint8)
        self.changed = self._rectangle(self.baseline, (42, 56), (124, 127))
        self.persistent = self._rectangle(self.baseline, (45, 59), (127, 130))
        self.rel_location = self._rectangle(self.baseline, (160, 58), (241, 129))

        self.reference_path = self._write("reference.png", self.baseline)
        self.observed_path = self._write("observation.png", self.changed)
        self.followup_path = self._write("followup.png", self.persistent)
        self.clear_path = self._write("cleared.png", self.baseline)
        self.moved_path = self._write("moved.png", self.rel_location)

    @staticmethod
    def _rectangle(image: np.ndarray, top_left: tuple[int, int], bottom_right: tuple[int, int]) -> np.ndarray:
        result = image.copy()
        cv2.rectangle(result, top_left, bottom_right, (242, 242, 242), thickness=-1)
        return result

    def _write(self, filename: str, pixels: np.ndarray) -> Path:
        destination = self.directory / filename
        ok, encoded = cv2.imencode(".png", pixels)
        if not ok:
            self.fail(f"OpenCV could not encode test fixture: {filename}")
        destination.write_bytes(encoded.tobytes())
        return destination

    def _run(self, **overrides: object) -> VisionWorkflowResult:
        parameters: dict[str, object] = {
            "reference_path": self.reference_path,
            "observed_path": self.observed_path,
        }
        parameters.update(overrides)
        return run_visual_workflow(**parameters)

    def test_initial_observation_detects_change(self) -> None:
        result = self._run()
        self.assertTrue(result.observation.change_detected)
        self.assertGreater(result.observation.changed_pixel_fraction, 0.01)
        self.assertGreaterEqual(result.observation.regions_total, 1)
        self.assertEqual(result.decision.status, "awaiting_followup")
        self.assertEqual(result.decision.next_action, "request_second_observation")
        self.assertIsNone(result.verification)
        self.assertFalse(result.decision.confirmed_pixel_persistence)

    def test_no_change_continues_monitoring(self) -> None:
        result = self._run(observed_path=self.clear_path)
        self.assertFalse(result.observation.change_detected)
        self.assertEqual(result.observation.regions_total, 0)
        self.assertEqual(result.decision.status, "no_significant_change")
        self.assertEqual(result.decision.next_action, "continue_monitoring")
        self.assertFalse(result.decision.confirmed_pixel_persistence)

    def test_followup_confirms_spatial_persistence(self) -> None:
        result = self._run(followup_path=self.followup_path)
        self.assertIsNotNone(result.verification)
        self.assertTrue(result.verification.persistent_region)
        self.assertTrue(result.decision.confirmed_pixel_persistence)
        self.assertEqual(result.decision.status, "persistent_change_observed")
        self.assertEqual(result.decision.next_action, "request_human_review")

    def test_followup_without_change_rejects_persistence(self) -> None:
        result = self._run(followup_path=self.clear_path)
        self.assertIsNotNone(result.verification)
        self.assertFalse(result.verification.observation.change_detected)
        self.assertFalse(result.verification.persistent_region)
        self.assertFalse(result.decision.confirmed_pixel_persistence)
        self.assertEqual(result.decision.next_action, "review_conditions_or_repeat")

    def test_disjoint_followup_does_not_count_as_persistence(self) -> None:
        result = self._run(followup_path=self.moved_path)
        self.assertTrue(result.observation.change_detected)
        self.assertTrue(result.verification.observation.change_detected)
        self.assertFalse(result.verification.persistent_region)
        self.assertEqual(result.decision.next_action, "review_conditions_or_repeat")

    def test_followup_is_not_consumed_without_initial_change(self) -> None:
        result = self._run(observed_path=self.clear_path, followup_path=self.followup_path)
        self.assertIsNone(result.verification)
        self.assertEqual(result.decision.next_action, "continue_monitoring")
        self.assertEqual(set(result.source_sha256), {"reference", "observation"})
        self.assertTrue(any("not processed" in warning.lower() for warning in result.warnings))

    def test_out_of_roi_changes_are_not_reported(self) -> None:
        result = self._run(roi=(0, 0, 32, 32))
        self.assertFalse(result.observation.change_detected)
        self.assertEqual(result.observation.roi_xywh, (0, 0, 32, 32))
        self.assertEqual(result.decision.next_action, "continue_monitoring")

    def test_roi_containing_changed_area_detects_it(self) -> None:
        roi = (35, 50, 100, 90)
        result = self._run(roi=roi)
        self.assertTrue(result.observation.change_detected)
        self.assertEqual(result.observation.roi_xywh, roi)
        for region in result.observation.regions:
            x, y, w, h = region.bbox_xywh
            self.assertGreaterEqual(x, roi[0])
            self.assertGreaterEqual(y, roi[1])
            self.assertLessEqual(x + w, roi[0] + roi[2])
            self.assertLessEqual(y + h, roi[1] + roi[3])

    def test_single_changed_pixel_is_filtered_as_noise(self) -> None:
        noisy = self.baseline.copy()
        noisy[40, 40] = (255, 255, 255)
        noisy_path = self._write("one_pixel_noise.png", noisy)
        result = self._run(observed_path=noisy_path)
        self.assertFalse(result.observation.change_detected)
        self.assertEqual(result.decision.next_action, "continue_monitoring")

    def test_small_uniform_brightness_shift_is_below_threshold(self) -> None:
        brighter = np.full_like(self.baseline, 30)
        brighter_path = self._write("brightness_shift.png", brighter)
        result = self._run(observed_path=brighter_path, threshold=32)
        self.assertFalse(result.observation.change_detected)

    def test_identical_dimensions_are_required(self) -> None:
        smaller_path = self._write("smaller.png", np.full((120, 130, 3), 20, dtype=np.uint8))
        with self.assertRaisesRegex(ValueError, "identical dimensions"):
            self._run(observed_path=smaller_path)

    def test_unreadable_image_is_rejected(self) -> None:
        broken = self.directory / "broken.png"
        broken.write_bytes(b"this is not an image")
        with self.assertRaisesRegex(ValueError, "Cannot decode"):
            self._run(observed_path=broken)

    def test_missing_image_is_rejected(self) -> None:
        with self.assertRaises((FileNotFoundError, OSError)):
            self._run(observed_path=self.directory / "missing.png")

    def test_bad_roi_is_rejected(self) -> None:
        for roi in ((255, 0, 4, 20), (0, 190, 20, 4), (0, 0, 0, 5), (-1, 0, 10, 10)):
            with self.subTest(roi=roi):
                with self.assertRaises(ValueError):
                    self._run(roi=roi)

    def test_invalid_threshold_is_rejected(self) -> None:
        for threshold in (0, 256):
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    self._run(threshold=threshold)

    def test_invalid_min_region_fraction_is_rejected(self) -> None:
        for minimum in (0.0, -0.1, 0.6):
            with self.subTest(minimum=minimum):
                with self.assertRaises(ValueError):
                    self._run(min_region_fraction=minimum)

    def test_annotation_creates_readable_image(self) -> None:
        output_path = self.directory / "results" / "annotated.png"
        result = self._run(annotated_path=output_path)
        self.assertTrue(output_path.is_file())
        self.assertEqual(result.annotated_output, str(output_path.resolve()))
        annotated = cv2.imdecode(np.frombuffer(output_path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(annotated)
        self.assertEqual(annotated.shape, self.baseline.shape)

    def test_invalid_annotation_extension_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "PNG or JPEG"):
            self._run(annotated_path=self.directory / "annotation.txt")

    def test_hashes_correspond_to_actual_input_files(self) -> None:
        result = self._run(followup_path=self.followup_path)
        expected = {
            "reference": self.reference_path,
            "observation": self.observed_path,
            "followup": self.followup_path,
        }
        self.assertEqual(set(result.source_sha256), set(expected))
        for key, path in expected.items():
            with self.subTest(source=key):
                self.assertEqual(result.source_sha256[key], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_first_observation_runs_once_without_followup(self) -> None:
        spy = Mock(wraps=inspect)
        result = self._run(_inspect=spy)
        self.assertEqual(spy.call_count, 1)
        self.assertIsNone(result.verification)

    def test_second_observation_causes_a_real_second_inspection(self) -> None:
        spy = Mock(wraps=inspect)
        result = self._run(followup_path=self.followup_path, _inspect=spy)
        self.assertEqual(spy.call_count, 2)
        self.assertEqual(spy.call_args.kwargs["followup_path"], self.followup_path)
        self.assertTrue(result.verification.persistent_region)
        self.assertEqual(
            [(event["phase"], event["result"]) for event in result.events if event["phase"] in {"act", "verify"}],
            [("act", "process_supplied_followup_image"), ("verify", "persistent_pixel_change")],
        )

    def test_workflow_does_not_process_followup_when_first_is_clear(self) -> None:
        spy = Mock(wraps=inspect)
        self._run(observed_path=self.clear_path, followup_path=self.followup_path, _inspect=spy)
        self.assertEqual(spy.call_count, 1)

    def test_evidence_is_json_serializable_and_never_safety_certified(self) -> None:
        for followup in (None, self.followup_path, self.clear_path):
            with self.subTest(followup=followup):
                result = self._run(followup_path=followup)
                document = result.to_dict()
                self.assertEqual(json.loads(json.dumps(document)), document)
                self.assertFalse(document["safety_certified"])
                self.assertFalse(document["decision"]["safety_certified"])
                self.assertEqual(document["verification_scope"], "persistent_pixel_change_only")
                self.assertEqual(document["verified"], result.decision.confirmed_pixel_persistence)

    def test_cv2_version_in_report_matches_installed_opencv(self) -> None:
        result = self._run()
        self.assertEqual(result.cv2_version, cv2.__version__)
        self.assertEqual(result.to_dict()["opencv5_confirmed"], cv2.__version__.split(".", 1)[0] == "5")
        if not result.to_dict()["opencv5_confirmed"]:
            self.assertTrue(any("OpenCV 5" in warning for warning in result.warnings))

    def test_audit_accepts_real_reports(self) -> None:
        plain = inspect(self.reference_path, self.observed_path)
        observed, verification, hashes = audit_engine_report(plain)
        self.assertTrue(observed.change_detected)
        self.assertIsNone(verification)
        self.assertEqual(set(hashes), {"reference", "observation"})
        followup = inspect(self.reference_path, self.observed_path, followup_path=self.followup_path)
        observed2, verification2, hashes2 = audit_engine_report(followup)
        self.assertEqual(observed, observed2)
        self.assertTrue(verification2.persistent_region)
        self.assertIn("followup", hashes2)
        audit_followup_pair(plain, followup)

    def test_audit_rejects_falsely_verified_initial_report(self) -> None:
        report = inspect(self.reference_path, self.observed_path)
        report["verified"] = True
        with self.assertRaises(VisionSchemaError):
            audit_engine_report(report)

    def test_audit_rejects_inconsistent_decision(self) -> None:
        report = inspect(self.reference_path, self.observed_path)
        report["decision"] = "persistent_change_review"
        with self.assertRaises(VisionSchemaError):
            audit_engine_report(report)

    def test_audit_rejects_forged_or_missing_hashes(self) -> None:
        for mutation in ("not-a-sha256", None):
            report = inspect(self.reference_path, self.observed_path)
            report["source_sha256"]["observation"] = mutation
            with self.subTest(value=mutation):
                with self.assertRaises(VisionSchemaError):
                    audit_engine_report(report)
        report = inspect(self.reference_path, self.observed_path)
        report["source_sha256"]["followup"] = "a" * 64
        with self.assertRaises(VisionSchemaError):
            audit_engine_report(report)

    def test_audit_rejects_region_outside_roi(self) -> None:
        report = inspect(self.reference_path, self.observed_path)
        report["observation"]["regions"][0]["bbox_xywh"] = [400, 400, 20, 20]
        with self.assertRaises(VisionSchemaError):
            audit_engine_report(report)

    def test_followup_audit_rejects_changed_source_identity(self) -> None:
        initial = inspect(self.reference_path, self.observed_path)
        final = inspect(self.reference_path, self.observed_path, followup_path=self.followup_path)
        tampered = copy.deepcopy(final)
        tampered["source_sha256"]["reference"] = "f" * 64
        with self.assertRaises(VisionSchemaError):
            audit_followup_pair(initial, tampered)

    def test_followup_audit_rejects_inconsistent_observations(self) -> None:
        initial = inspect(self.reference_path, self.observed_path)
        final = inspect(self.reference_path, self.observed_path, followup_path=self.followup_path)
        tampered = copy.deepcopy(final)
        tampered["observation"]["changed_pixel_fraction"] = 0.999
        with self.assertRaises(VisionSchemaError):
            audit_followup_pair(initial, tampered)

    def test_workflow_rejects_missing_opencv_version(self) -> None:
        report = inspect(self.reference_path, self.observed_path)
        report.pop("cv2_version")
        fake_inspect = Mock(return_value=report)
        with self.assertRaises(VisionSchemaError):
            self._run(_inspect=fake_inspect)

    def test_decision_rejects_semantic_or_safety_certification(self) -> None:
        report = inspect(self.reference_path, self.observed_path, followup_path=self.followup_path)
        observed, verification, _hashes = audit_engine_report(report)
        decision = choose_next_action(observed, verification)
        self.assertTrue(decision.confirmed_pixel_persistence)
        self.assertTrue(decision.human_review_required_for_safety)
        self.assertFalse(decision.safety_certified)
        self.assertIn("pixel", decision.reason.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
