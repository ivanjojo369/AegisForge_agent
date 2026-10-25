# Quipu Vision — OpenCV AI Competition 2026

**Submission package draft | Status: local MVP validated, AWS runtime NOT YET VERIFIED**

> This document is a working technical report and demonstration guide. Do not describe the AWS deployment as completed unless it has actually been run on AWS. All performance values below must be measured before submission.

## Project summary

Quipu Vision is an evidence-first visual change inspection capability integrated into the AegisForge A2A agent runtime. It compares a reference image to an observed image with OpenCV, identifies regions of significant pixel change, and requests another observation when evidence is insufficient. With a follow-up image, it checks whether the visual change persists and returns an auditable next action, such as asking for human review. The project is intentionally bounded: persistence of pixel changes is not object recognition, hazard detection, or a safety guarantee.

**Target use case:** fixed-camera inspection of a workstation, shared physical space, or process checkpoint in which an operator wants to detect and review meaningful changes rather than trust a single ambiguous frame.

**Potential users:** prototype automation developers, quality-control experimenters, facility operators, and research teams.

## Problem

A single frame difference can arise from genuine changes or ordinary nuisance factors such as shadows, reflections, camera shifts, and illumination. Systems that immediately treat a detected difference as a verified event are unreliable. Quipu Vision separates **observed change**, **requested re-observation**, **confirmed pixel persistence**, and **human review**. Its policy deliberately does not claim semantic object identification or physical safety certification.

## Implemented architecture (locally demonstrated)

```
A2A client (DataPart base64 or FilePart with image bytes)
    -> AegisForge a2a_server.py
    -> Executor vision dispatch (explicit capability="quipu_vision")
    -> QuipuVisionPlugin 0.4.0 (input validation and bounded staging)
    -> Quipu-Vision workflow.py
       -> core.py (OpenCV frame comparison / changed regions)
       -> verifier.py (evidence coherence and follow-up consistency)
       -> schemas.py (structured observations and decisions)
    -> A2A task result + quipu_vision_result artifact
```

**AWS design (prepared, NOT deployed):** client -> AWS IAM-protected HTTPS Lambda Function URL -> Lambda image stored in Amazon ECR -> AegisForge + Quipu Vision OpenCV 5 runtime -> A2A response. The `vision_lambda` Docker target and CloudFormation template are prepared; no successful deployment, cloud execution logs, or AWS image-processing trace has been provided.

## OpenCV 5 processing

- Image formats: PNG, JPEG, BMP, WebP; authorized local file paths or image bytes supplied via A2A.
- Core: blur/difference/threshold/morphology/contour-based changed-region extraction with optional rectangular ROI and tunable thresholds.
- Reference -> observed: identify changed regions and request a second observation if a change is detected and not yet confirmed.
- Reference -> observed -> follow-up: evaluate whether the changed region persists, then request human review or another inspection as appropriate.
- Structured evidence: event trace, regions, status, follow-up state, and provenance (including hashes where provided by the engine).
- Security boundaries: bounded image sizes, allowed input roots, format validation, controlled temporary file staging, automatic cleanup, and errors that do not masquerade as validated results.

**Limitations:** camera repositioning, lighting changes, reflections, deformable motion, compression artifacts, and occlusion can affect false positive/negative behavior. No real-time video streaming, autonomous capture, object classification, or physical safety certification is claimed.

## Reproducible implementation (existing project structure)

```
AegisForge_agent/
  Dockerfile
  .dockerignore
  infra/aws/vision-lambda.yaml           # prepared, deployment unverified
  Quipu-Vision/
    pyproject.toml
    src/quipu_vision/
      core.py
      schemas.py
      verifier.py
      workflow.py
    examples/demo/
      referencia.png
      observacion.png
      seguimiento.png
      sin_cambio.png
  src/aegisforge/
    a2a_server.py
    executor.py
    plugins/examples/quipu_vision.py
```

Local container build:

```powershell
docker build --target vision -t aegisforge-vision:0.4 .
```

Local run: use port 8001 without collision, and confirm Vision is enabled in the container image configuration:

```powershell
docker run --rm --name quipu-vision -p 127.0.0.1:8001:8001 aegisforge-vision:0.4
```

Query `http://127.0.0.1:8001/health` and `http://127.0.0.1:8001/vision/capabilities`; only count this as live processing evidence once actual image-bearing A2A `message/send` requests complete successfully.

## Recorded evidence supplied by developer (23–24 October 2026)

| Environment | Check | Observed result | Evidence status |
|---|---|---|---|
| Windows Python/A2A | `/vision/capabilities` | plugin 0.4.0, OpenCV 5.0.0, A2A true | Confirmed from shared console output |
| Windows A2A | `verify` with three supplied images | Task completed, verified true, next action `request_human_review` | Confirmed from shared console output |
| Windows A2A | `inspect` with inline base64 images | Task completed, verified false, next action `request_second_observation` | Confirmed from shared console output |
| Docker Linux | `docker build --target vision` | Build finished, image `aegisforge-vision:0.4` exported | Confirmed from shared build log |
| Docker Linux | `/health` and `/vision/capabilities` | Healthy; plugin 0.4.0; OpenCV 5.0.0; A2A true | Confirmed from shared console output |
| Docker Linux | image-bearing A2A `verify` | Task completed, verified true, next action `request_human_review` | Confirmed from shared console output |
| AWS | Image pushed to ECR / running Lambda / image-bearing invocation | No successful deployment evidenced | **PENDING — mandatory contest requirement** |

**Interpretation:** `verified=true` in the above visual scenario means persistent *pixel difference*, not semantic or safety verification. The synthetic demo does not establish accuracy on real-world images.

## Evaluation to perform before final submission

Use a small labeled set of diverse, consented, fixed-camera images. For every case record ground-truth change presence, predicted change, status, number of changed regions, follow-up result, next action, processing latency, and failure mode. Run all cases with the exact OpenCV 5 container intended for the demo.

| Evaluation condition | Ground truth | Metric to calculate | Status |
|---|---|---|---|
| Unchanged scene | No persistent change | False-positive rate | Not yet measured on diverse real images |
| Stable new object or region | Persistent change | True-positive rate and follow-up success | Demonstrated synthetically; larger evaluation pending |
| Temporary change removed in follow-up | Nonpersistent change | Correct rejection rate | Synthetic scenario demonstrated |
| Illumination/shadows | No physical object change | False-positive rate | Pending |
| Camera displacement | Ambiguous | Re-observation / rejection behavior | Pending |
| Blurred or damaged inputs | Invalid or low-quality evidence | Fail-safe error handling | Pending formal report |
| Repeated benchmark runs | Same input | Median, p95 processing time | Pending measurement |

Calculate precision = TP/(TP+FP), recall = TP/(TP+FN), and false-positive rate = FP/(FP+TN) only when ground-truth labels and denominators are available. Do not present guessed percentages.

## Agentic behavior trace to demonstrate

1. Send a reference and observed image via an A2A request.
2. OpenCV 5 identifies pixel-change evidence.
3. The workflow returns `request_second_observation`, which changes the requested next action.
4. Supply a follow-up image in a new `verify` operation.
5. The workflow verifies whether the change persists and returns `request_human_review` when appropriate.
6. Show the A2A artifact and evidence metadata for both steps.

**Scope clarification:** This is a bounded human-in-the-loop visual workflow, not automated camera capture. The follow-up image is supplied by the client/operator rather than autonomously acquired by the agent.

## Video outline (maximum five minutes)

- 0:00–0:35 — project problem, target scenario, limits.
- 0:35–1:10 — architecture: A2A server, Executor, plugin, workflow, OpenCV 5, verifier.
- 1:10–2:15 — Windows or Docker demonstration: first observation and `request_second_observation`.
- 2:15–3:15 — follow-up and `request_human_review`, show JSON evidence and regions.
- 3:15–4:00 — error/edge case and responsible-use limitations.
- 4:00–4:35 — measured evaluation results (insert actual numbers only).
- 4:35–5:00 — AWS demonstration **only if AWS execution succeeds**; otherwise describe it as pending, not deployed.

## Final submission checklist

- [x] OpenCV 5 observed in actual Windows and Docker runtime.
- [x] A2A image inspection and follow-up shown in shared console output.
- [x] Linux Docker image built and local image-processing request completed.
- [ ] A meaningful component actually executes on AWS and the result is recorded.
- [ ] Quantitative real-image evaluation with limitations and failure cases.
- [ ] Final technical report with measured results and evidence screenshots.
- [ ] Judge-accessible source repository or archive (no secrets/private data).
- [ ] Reproducible deployment/build/test documentation.
- [ ] Architecture diagram showing **actual**, not hypothetical, AWS use.
- [ ] Endpoint or scheduled live demonstration.
- [ ] Judge-accessible video no longer than five minutes.
- [ ] Registration/eligibility and any required guardian permission/platform paperwork independently confirmed.

## Compliance notes

The competition requires substantive OpenCV 5 analysis **and** a meaningful AWS component. Docker running locally does not satisfy the AWS requirement. A screen-share demo can fulfill the demonstration modality, but does not replace running a meaningful component on AWS. Organizers also require judge-accessible code, technical documentation, evaluation, architecture diagram, and a video no longer than five minutes.

Official pages:
- https://opencv26.devpost.com/
- https://opencv26.devpost.com/rules

The Devpost listing displays a deadline of **Oct 26, 2026 at 11:45 p.m. PDT**, while narrative text mentions **11:59 p.m. Pacific**; use 11:45 p.m. PDT as a conservative submission cutoff.

The Devpost overview eligibility summary says above the legal age of majority, while its detailed rules say 13+ with parent/guardian permission for minors. Resolve the discrepancy with the organizer and observe any required platform forms; do not assume the discrepancy authorizes submission.
