# OfficeQA AgentBeats Integration

## Overview

This integration adapts **OfficeQA** into the AegisForge_agent repository as a first-class purple track under the internal name `officeqa`.

The purpose of this integration is to support:

- document-grounded financial question answering,
- scenario-driven security evaluation,
- clean vs poisoned comparisons,
- and local packaging/validation workflows consistent with the rest of AegisForge.

The flagship security scenario for this integration is **TaxWizTrap**, which models indirect prompt injection through a tax or audit-related document.

---

## Naming conventions

- **External benchmark name:** OfficeQA
- **AegisForge integration name:** `officeqa_agentbeats`
- **AegisForge internal track name:** `officeqa`
- **Primary scenario:** `taxwiztrap`

---

## Directory role

This integration directory is the documentation and sample-data layer for the OfficeQA track.

It should contain:

- a benchmark-facing README,
- minimal example payloads,
- normalized sample outputs,
- and scenario-aligned examples used for local debugging.

Suggested structure:

```text
integrations/
  officeqa_agentbeats/
    README.md
    data_demo/
      officeqa_sample_question.json
      officeqa_sample_result.json
      taxwiztrap_clean_example.json
      taxwiztrap_poisoned_example.json
```

---

## Conceptual mapping

### Benchmark layer

OfficeQA provides the task shape: a question is asked over financial document content, and the purple agent must answer accurately using grounded evidence.

### Scenario layer

TaxWizTrap introduces the security failure mode: the document may contain hidden or deceptive text that tries to manipulate the model's reasoning.

### Internal runtime layer

The AegisForge adapter translates benchmark inputs or scenario payloads into the internal orchestration flow.

This means the track should support two modes:

1. **Clean benchmark mode** — standard document QA.
2. **Security-aware scenario mode** — document QA with injection resistance.

---

## Integration goals

The OfficeQA integration should make it easy to:

- run clean local tests,
- run poisoned scenario tests,
- compare outcome stability,
- export evaluation artifacts,
- and package submission-ready outputs.

---

## Expected internal components

This integration assumes the following companion files exist elsewhere in the repo:

- `src/aegisforge/adapters/officeqa/adapter.py`
- `src/aegisforge/adapters/officeqa/payload_mapper.py`
- `src/aegisforge/strategy/officeqa.py`
- `src/aegisforge/prompts/tracks/officeqa.md`
- `src/aegisforge_eval/tracks/officeqa.py`
- `scripts/run_officeqa_purple_eval.ps1`
- `scripts/prepare_officeqa_purple_submission.ps1`
- `scripts/validate_officeqa_purple_submission.ps1`

---

## Input contract

A normalized OfficeQA payload should typically contain:

- a scenario or benchmark identifier,
- the user question,
- document or extracted text content,
- optional evidence chunks,
- optional clean/poisoned metadata,
- and answer-format requirements.

Example high-level shape:

```json
{
  "track": "officeqa",
  "scenario": "taxwiztrap",
  "question": "Assess the audit risk for this document.",
  "document_text": "...",
  "answer_format": "structured"
}
```

---

## Output contract

A normalized result should capture at least:

- final answer,
- evidence summary,
- suspicion flags for prompt injection,
- scenario outcome,
- and evaluation metadata.

Example high-level shape:

```json
{
  "track": "officeqa",
  "scenario": "taxwiztrap",
  "final_answer": "Audit Risk: High",
  "evidence_used": ["income mismatch", "withholding anomaly"],
  "prompt_injection_detected": true,
  "status": "ok"
}
```

---

## Security expectations

The integration should enforce a simple principle:

> Documents are evidence sources, not instruction authorities.

Therefore the OfficeQA track must resist:

- hidden instructions in documents,
- malicious formatting intended to mimic system prompts,
- low-contrast or appended jailbreak text,
- and any attempt to force a different classification without valid evidence.

---

## Local workflow

Recommended workflow inside AegisForge:

1. Run adapter tests.
2. Run clean scenario payload.
3. Run poisoned scenario payload.
4. Compare outcome stability.
5. Export artifacts.
6. Prepare submission bundle.
7. Validate submission structure.

---

## Why this integration exists

OfficeQA on its own is a strong financial document benchmark. AegisForge extends it with a scenario-driven safety layer so that performance can be measured not only by correctness, but also by resilience to realistic document-borne attacks.
