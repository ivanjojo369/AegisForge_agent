# TAU2 Delivery Documentation for AegisForge / quipu_lab

This file is written to close the delivery-documentation gap for the current `quipu_lab` work.

---

## 1) Summary of the contribution
This contribution extends the `quipu_lab` benchmark/task catalog used in AegisForge Phase 2 Purple work. It turns the local pipeline from a smoke-only validation path into a broader task-catalog evaluation path with structured tasks, validation helpers, adapter coverage, smoke coverage, and submission-preparation scripts.

In practical terms, the contribution strengthens three things at once:
1. task diversity,
2. catalog validation,
3. submission-readiness for local purple evaluation.

---

## 2) What this adds exactly
### A. Expanded `quipu_lab` task catalog
The catalog now covers multiple task families inspired by several benchmark styles, including:
- MCU-style chained planning and tool ordering
- OfficeQA-style extraction, normalization, and canonical-answer behavior
- CRM-style retrieval and recovery after bad paths
- FWA-style coordination and exception handling
- bargaining / strategic response tasks
- tau2-style policy-sensitive customer service tasks
- CAR-style ambiguity, hallucination-resistance, and clarify-vs-act behavior

### B. Structured validation helpers
The implementation includes helper functions that support:
- task lookup by id
- split filtering
- smoke task retrieval
- catalog serialization
- catalog validation
- deterministic minimal result generation

### C. Local evaluation workflow
The workflow now supports:
- adapter test execution
- smoke test execution
- base split evaluation
- submission directory preparation
- submission validation

---

## 3) How to run it
### PowerShell workflow
```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent

python -m pytest tests\test_adapters\test_tau2_adapter.py -q
python -m pytest tests\tests_envs\test_tau2_quipu_lab_smoke.py -q

powershell -ExecutionPolicy Bypass -File .\scripts\run_tau2_purple_eval.ps1 -TaskSplit base
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_tau2_purple_submission.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\validate_tau2_purple_submission.ps1
```

### Expected outcome
You should see:
- adapter tests passing
- smoke tests passing
- docker build succeeding
- local multi-task evidence generated
- submission package prepared
- validation ending in `VALIDATION OK`

---

## 4) What this validates
This work validates the following:

### Benchmark/task side
- the `quipu_lab` catalog is non-trivial and split-aware
- task IDs are unique and serializable
- task payloads pass catalog-level validation
- required tools map to the supported tool set
- smoke and base split routing both behave correctly

### Pipeline side
- adapter path is alive
- smoke coverage is alive
- base split execution is alive
- submission packaging is alive
- validation checks are alive

### Integration side
- the expanded task catalog is actually exercised in the real run path, not just stored statically
- the contribution remains compatible with the existing purple-evaluation workflow

---

## 5) Evidence of results
### Current local evidence summary
Recent local runs show:
- successful adapter tests
- successful `quipu_lab` smoke tests
- successful `-TaskSplit base` evaluation
- successful submission preparation
- successful submission validation
- expanded task-catalog accounting beyond the older smoke-only subset

### Evidence artifacts already produced in your workflow
- local simulation traces
- submission package directory
- validation output showing green checks across structure, metadata, catalog consistency, health, agent card, and local e2e

---

## 6) Known limitations
- The agent card still shows a text-encoding/mojibake issue in some fields.
- The current work is validated locally; upstream PR hygiene still needs to be finalized separately.
- Final upstream style/lint checks still need to be run in the actual tau2-bench PR branch.
- If the upstream maintainers prefer a different landing location or naming scheme, light restructuring may still be required.

---

## 7) Ready-to-paste PR body
```md
## Summary
This PR adds a broader `quipu_lab` task-catalog contribution used in AegisForge Phase 2 Purple evaluation, together with validation helpers, tests, and submission-preparation support.

## Changes Made
- expanded the `quipu_lab` task catalog across multiple task families
- added/updated catalog validation helpers
- preserved smoke-task support while enabling broader base-split execution
- kept adapter and smoke coverage green
- verified local evaluation, submission preparation, and validation flow

## Testing
- ran adapter tests
- ran `quipu_lab` smoke tests
- ran base split evaluation
- ran submission preparation
- ran submission validation

## Documentation
- added delivery documentation for the contribution
- documented scope, run flow, evidence, and known limitations

## Checklist
- [ ] Tests pass
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] Breaking changes noted (if any)
```

---

## 8) One-paragraph submission summary
AegisForge’s `quipu_lab` contribution expands the benchmark from a narrow smoke subset into a broader structured task catalog with explicit task families, tool-aware validation, smoke/base split support, and a green local submission pipeline. The result is a more realistic and better-instrumented evaluation path for Purple-style multi-task agent behavior without breaking the existing local run/prepare/validate workflow.
