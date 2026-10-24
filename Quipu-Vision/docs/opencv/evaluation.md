# Quipu Vision — Evaluation Report

**Submission:** OpenCV AI Competition 2026  
**Component evaluated:** Quipu Vision visual change detection and spatial-persistence workflow  
**Status:** Local synthetic evaluation complete; real-world validation and AWS deployment evidence pending  
**Reported execution environment:** OpenCV **5.0.0**, Windows/PowerShell, Python virtual environment associated with AegisForge  
**Result source:** Console output provided from the user's local run. The full JSON report remains on the user's machine and was not independently inspected for this report.

## 1. Executive summary

Quipu Vision compares a reference image with an observed image, identifies visual differences within an optional region of interest (ROI), and can assess whether a changed image region persists spatially in a follow-up observation. It produces structured evidence and a next-action decision through its workflow. **It does not recognize object identity, establish the cause of a change, or certify physical safety.**

On **24 labeled synthetic scenarios**, the initial-change stage achieved **75.00% accuracy**, **75.00% precision**, **85.71% recall**, and **0.8000 F1**. The spatial-persistence evaluation, covering **7 scenarios with follow-up labels**, achieved **85.71% accuracy**, **100.00% precision**, **80.00% recall**, and **0.8889 F1**. The workflow recorded **zero execution errors**. A separate functional suite passed **33 of 33 tests** under OpenCV 5.0.0.

The results expose specific weaknesses: stronger illumination changes, shadows, camera motion, and glare may trigger a false change, while low-contrast changes may be missed. Because the scenarios are synthetic and correlated, these figures **must not be treated as estimates of performance on real scenes**.

## 2. Dataset and ground truth

| Property | Specification |
|---|---|
| Dataset | `Quipu Vision Fixed-Camera Synthetic Evaluation v1` |
| Origin | Programmatically generated synthetic images |
| Scenarios | 24 |
| Image assets | 23 PNG files, reused across scenarios |
| Scene | Stylized static walkway, 320 × 240 pixels, BGR PNG |
| Reference strategy | Shared base scene; cases are **not statistically independent** |
| Initial-change labels | Meaningful new image structure **inside the ROI**; illumination-only change, blur, sensor noise, and camera jitter are labeled negative |
| Persistence labels | Spatial overlap of changed regions across observations; **not object identity or tracking** |
| Follow-up scenarios | 7 |
| Labeling | Known synthetic image-generation operations, not model predictions |
| Integrity | Image hashes verified against `manifest.json` with SHA-256 before evaluation |
| Detection threshold | `32` |
| Minimum region fraction | `0.004` |

The test collection covers unchanged scenes, sensor noise, small brightness changes, strong lighting, shadows, glare, camera jitter, blur, object appearance and disappearance, object position changes, multiple regions, thin and low-contrast changes, and ROIs. These are controlled test conditions, not a real-world deployment dataset.

### Paths within the project

```text
AegisForge_agent/
├── Quipu-Vision/
│   ├── examples/evaluation/
│   │   ├── evaluate.py
│   │   ├── manifest.json
│   │   └── images/
│   └── tests/test_workflow.py
├── docs/competitions/opencv/evaluation.md    # this report
└── outputs/quipu_vision/evaluation-results.json  # locally generated, not necessarily committed
```

## 3. Evaluation protocol

The evaluator reads `manifest.json`, checks the listed PNG files and their SHA-256 hashes, applies the manifest's detector settings, and calls `quipu_vision.workflow.run_visual_workflow()` for each labeled scenario. For cases with follow-up images, it also records the workflow's spatial-persistence decision. Predictions are compared with the corresponding labels.

The reported metrics are calculated as follows:

- **Accuracy** = (TP + TN) / (TP + TN + FP + FN)
- **Precision** = TP / (TP + FP)
- **Recall** = TP / (TP + FN)
- **F1** = 2 × TP / (2 × TP + FP + FN)

Here, **TP** denotes a correctly detected change/persistence, **TN** a correctly rejected change/persistence, **FP** an incorrect positive, and **FN** a missed positive. Initial-change and persistence evaluations use **different denominators and targets**; their metrics must not be pooled.

## 4. Results: initial scene-change detection

### Confusion matrix

| Ground truth \ Prediction | Change detected | No change detected | Total |
|---|---:|---:|---:|
| Meaningful change | **12 (TP)** | **2 (FN)** | 14 |
| No meaningful change | **4 (FP)** | **6 (TN)** | 10 |
| **Total** | **16** | **8** | **24** |

| Metric | Result |
|---|---:|
| Accuracy | **75.00%** (18/24) |
| Precision | **75.00%** (12/16) |
| Recall | **85.71%** (12/14) |
| F1 | **0.8000** |
| False-positive rate | 40.00% (4/10) |
| False-negative rate | 14.29% (2/14) |

These measures describe classification of **labeled synthetic scene changes**, not semantic object-detection accuracy. In particular, an image can differ substantially because of lighting, even when the synthetic ground truth says no meaningful object change occurred.

## 5. Results: spatial persistence

The persistence subset contains seven scenarios with a follow-up image and a persistence label. The prediction refers to **spatial overlap of a changed region** relative to the earlier observation.

### Confusion matrix

| Ground truth \ Prediction | Persistence confirmed | Persistence not confirmed | Total |
|---|---:|---:|---:|
| Spatial persistence present | **4 (TP)** | **1 (FN)** | 5 |
| No spatial persistence | **0 (FP)** | **2 (TN)** | 2 |
| **Total** | **4** | **3** | **7** |

| Metric | Result |
|---|---:|
| Accuracy | **85.71%** (6/7) |
| Precision | **100.00%** (4/4) |
| Recall | **80.00%** (4/5) |
| F1 | **0.8889** |
| False-positive rate | 0.00% (0/2) |
| False-negative rate | 20.00% (1/5) |

**Interpretation caution:** The single missed persistence label occurs in the low-contrast scenario also missed during initial detection. Because the workflow can decline to process a follow-up when the first observation contains no detected change, this is an **end-to-end pipeline result**, not proof that the persistence verifier independently failed.

## 6. Error analysis

The run reported **seven mismatched stage-level labels** across **six distinct scenario IDs**, with **zero execution errors**.

| Scenario ID | Stage | Expected | Observed | Observation |
|---|---|---|---|---|
| `no_change_strong_lighting` | Initial change | False | True | Large lighting-only change triggered a positive |
| `no_change_shadow` | Initial change | False | True | Shadow triggered a positive |
| `no_change_camera_motion` | Initial change | False | True | Camera jitter triggered a positive |
| `no_change_glare` | Initial change | False | True | Glare triggered a positive |
| `low_contrast_object` | Initial change | True | False | Low-contrast change was missed |
| `low_contrast_persists` | Initial change | True | False | Low-contrast change was missed on first observation |
| `low_contrast_persists` | Spatial persistence | True | False | Spatial persistence was not confirmed end-to-end |

The **four initial false positives** are nuisance-variation cases. The **two initial false negatives** concern low contrast, and one of those also contributes to the persistence-stage false negative. Thus the seven mismatches **do not represent seven different input cases**.

Potential improvements to study, **not yet implemented or validated in this evaluation**, include frame registration, illumination normalization, more robust handling of shadows/glare, and a low-contrast detection strategy. Any changes to thresholds or preprocessing should be evaluated on a held-out set rather than tuning to these 24 cases alone.

## 7. Processing time

| Measurement | Reported value |
|---|---:|
| Mean elapsed time per evaluated workflow case | **8.11 ms** |
| 95th percentile (nearest-rank method) | **14.56 ms** |
| Timed cases | **24** |
| Workflow execution errors | **0** |

The evaluator times each call to `run_visual_workflow()` with `time.perf_counter()`. Timing therefore covers the evaluated **workflow call**, which may include file access and follow-up processing, rather than an isolated OpenCV kernel. The p95 is computed using the evaluator's nearest-rank method. No hardware specification, repeat-count study, concurrency test, cloud latency, or production throughput measurement was provided, so no general real-time-performance claim is made.

## 8. Functional and integrity tests

A separate local run of `unittest` under **OpenCV 5.0.0** reported:

```text
Ran 33 tests in 0.870s

OK
```

**Result: 33 passed, 0 reported failures/errors.** These tests exercise workflow behavior such as detecting and rejecting changes, ROI handling, follow-up-dependent decisions, persistence checks, image annotations, invalid inputs, evidence hashes, JSON serialization, and audit rejection of inconsistent reports.

This is a **functional correctness** result for the tested conditions, not a claim of 100% scene-change detection accuracy. It complements rather than replaces the benchmark in Sections 4–6.

## 9. Reproduction instructions

From PowerShell at the root of `AegisForge_agent/`, using a virtual environment with Quipu Vision installed and OpenCV 5.0.0 available:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent

$python = ".\.venv\Scripts\python.exe"

# Check the actual OpenCV version.
& $python -c "import cv2; print('OpenCV:', cv2.__version__)"

# Evaluate all 24 synthetic cases and save per-case results.
& $python ".\Quipu-Vision\examples\evaluation\evaluate.py" `
  --json-out ".\outputs\quipu_vision\evaluation-results.json"

# Run the functional workflow tests.
& $python -m unittest discover `
  -s ".\Quipu-Vision\tests" `
  -p "test_workflow.py" -v
```

The evaluator reads `Quipu-Vision/examples/evaluation/manifest.json` by default. It uses existing labeled PNGs, does not download datasets, and writes the machine-readable report to the chosen `--json-out` location.

The full local report was stated to be at:

```text
C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent\outputs\quipu_vision\evaluation-results.json
```

That file can support a later per-category analysis and audit of individual run records. The present document is grounded in the supplied console output and the known evaluator/manifest structure; it does **not** imply that the local JSON file has been uploaded or separately checked here.

## 10. Limitations and next validation steps

1. **Synthetic, shared-scene dataset:** 24 correlated scenarios cannot establish field accuracy, robustness across environments, or confidence intervals representative of a target population.
2. **Semantic limitation:** Difference detection and spatial overlap do not identify objects, prove an obstruction, track object identities, or certify safety.
3. **Observed failure modes:** Strong lighting, shadows, camera motion, glare, and low contrast materially affect the current predictions.
4. **Temporal limitation:** Follow-up images are supplied as inputs; this benchmark does not test automatic camera capture or live video acquisition.
5. **Performance limitation:** Reported latency is local, single-run workflow elapsed time, not AWS latency or sustained throughput.
6. **Cloud status:** AWS deployment and a significant component actually running in AWS have **not** been validated by these results. Local Docker/A2A success must not be presented as AWS execution evidence.

Recommended next steps are to evaluate a separate set of permission-cleared real images, verify repeated-run latency on documented hardware, record representative annotated outputs and decisions, and conduct an authorized AWS deployment test if the competition submission requires it.

## 11. Conclusion

Quipu Vision successfully completed its local OpenCV 5.0.0 synthetic evaluation and passed its 33 functional tests. The quantitative results establish a **reproducible baseline**, including the limitations of fixed-threshold change detection under nuisance variation and low contrast. They do not demonstrate real-world reliability or safety performance. This report should accompany the runnable code, the labeled dataset, the generated JSON results, the architecture document, and an accurate deployment-status statement in the competition submission.
