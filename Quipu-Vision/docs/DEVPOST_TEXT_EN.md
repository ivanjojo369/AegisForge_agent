# Devpost-ready text — Quipu Vision

**Title:** Quipu Vision — Auditable Agentic Visual Inspection with OpenCV 5

**Tagline:** From pixel differences to bounded, evidence-driven requests for a follow-up observation and human review.

## Inspiration
Fixed-camera inspection often confuses real scene changes with nuisance variation: strong light, shadows, glare, and camera jitter. We wanted a workflow that preserves the measured evidence and avoids claiming certainty based on a single image comparison.

## What it does
Quipu Vision compares reference and observed images using OpenCV 5, identifies candidate changed regions, and issues a next-action recommendation. When a change is detected it asks for another observation; the client may then submit a third image for a second image-processing pass. If changed regions overlap spatially, the system requests human review. The workflow emits an auditable A2A artifact with source hashes, regions, events and conservative decisions. It does not classify objects or certify physical safety.

## How we built it
A separate Python package contains image decoding, Gaussian filtering, thresholding, morphology, contour-based region extraction, typed evidence and a consistency verifier. AegisForge integrates the workflow as an explicit Agent-to-Agent capability. A browser UI adds advisory image-quality analysis, region overlays and a trace of recorded decisions. An offline Evaluation Lab reads locally generated JSON from controlled OpenCV experiments. The Linux Docker image includes OpenCV 5.0.0; the backend plugin reports version 0.4.0 and the additional UI is a 0.5 candidate.

## Challenges
Strong lighting, glare, shadows and camera shifts can create false alerts; low-contrast changes can be missed. A second observation is supplied by a human/client rather than captured autonomously. Keeping provenance, validation, bounded resource use and cautious decision language aligned across OpenCV, A2A and the UI was important.

## Accomplishments
The user verified browser `inspect` and `verify` with OpenCV 5.0.0, including spatial persistence, a five-stage trace, and an interactive overlay. The previously executed Python functional suite passed 33/33 checks and the web/integration suite passed 63/63. A Linux Docker `vision` image built and started locally, exposing healthy A2A endpoints. On 24 correlated synthetic cases, default initial accuracy was 75.00% (F1 0.8000); 7 persistence cases yielded 85.71% accuracy (F1 0.8889). An exploratory threshold 40 increased initial accuracy to 79.17% on the same synthetic cases; it was not deployed as a verified production improvement.

## What's next
Run an authorized image-bearing workload on AWS and preserve the evidence; broaden evaluation using independently sourced permission-cleared images; improve robustness to illumination and low contrast; document reproducible cloud latency; and keep human review for consequential decisions.

## Built with
OpenCV 5.0.0, Python 3.11+, AegisForge, Agent-to-Agent protocol, JavaScript, HTML/CSS, Docker. **AWS Lambda, ECR, and CloudWatch are planned components until deployment evidence is captured.**

## Links to add before submission
- Independent repo: https://github.com/ivanjojo369/Quipu-Vision
- AegisForge integration: https://github.com/ivanjojo369/AegisForge_agent
- Final exact commits: [FILL AFTER PUBLISH]
- Demo video (public/unlisted <= 5 minutes): [FILL]
- Technical report: [FILL verified repository path]
- AWS evidence / function Region and logs (if deployed): [FILL ONLY IF VERIFIED]
- Judge demonstration: [FILL AUTHORIZED ENDPOINT OR ARRANGED SCREEN-SHARE]

**Suggested award path:** Agentic Vision, with explicitly bounded deterministic orchestration and human-supplied follow-up. Do not claim COOL execution.
