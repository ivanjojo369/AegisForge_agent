# CRMArenaPro AgentBeats Integration

## Overview

This integration adapts **CRMArenaPro** into the AegisForge_agent repository as a first-class purple track under the internal name `crmarena`.

The purpose of this integration is to support:

- CRM-style business task execution,
- scenario-driven security evaluation,
- extraction-resistance testing,
- and local packaging/validation workflows consistent with the rest of AegisForge.

The flagship security scenario for this integration is **SaleForceOneSpy**, which models prompt extraction and proprietary logic leakage in a CRM setting.

---

## Naming conventions

- **External benchmark name:** CRMArenaPro / Entropic CRMArenaPro
- **AegisForge integration name:** `crmarenapro_agentbeats`
- **AegisForge internal track name:** `crmarena`
- **Primary scenario:** `saleforceonespy`

---

## Directory role

This integration directory is the documentation and sample-data layer for the CRMArena track.

It should contain:

- a benchmark-facing README,
- minimal example tasks,
- normalized sample results,
- and scenario-aligned examples used for local debugging.

Suggested structure:

```text
integrations/
  crmarenapro_agentbeats/
    README.md
    data_demo/
      crm_task_sample.json
      crm_score_sample.json
      saleforceone_clean_example.json
      saleforceone_attack_example.json
```

---

## Conceptual mapping

### Benchmark layer

CRMArenaPro provides the task shape: the agent handles CRM-oriented work such as customer analysis, churn-related reasoning, prioritization, or business workflow assistance.

### Scenario layer

SaleForceOneSpy introduces the security failure mode: an adversarial user tries to extract proprietary churn logic, hidden system prompts, or tool details.

### Internal runtime layer

The AegisForge adapter translates benchmark tasks or scenario payloads into the internal orchestration flow.

This means the track should support two modes:

1. **Clean benchmark mode** — useful CRM task execution.
2. **Security-aware scenario mode** — useful execution with anti-extraction safeguards.

---

## Integration goals

The CRMArena integration should make it easy to:

- run clean local tasks,
- run extraction-attempt tasks,
- measure utility vs leakage tradeoffs,
- export evaluation artifacts,
- and package submission-ready outputs.

---

## Expected internal components

This integration assumes the following companion files exist elsewhere in the repo:

- `src/aegisforge/adapters/crmarena/adapter.py`
- `src/aegisforge/adapters/crmarena/payload_mapper.py`
- `src/aegisforge/strategy/crmarena.py`
- `src/aegisforge/prompts/tracks/crmarena.md`
- `src/aegisforge_eval/tracks/crmarena.py`
- `scripts/run_crmarena_purple_eval.ps1`
- `scripts/prepare_crmarena_purple_submission.ps1`
- `scripts/validate_crmarena_purple_submission.ps1`

---

## Input contract

A normalized CRMArena payload should typically contain:

- a scenario or benchmark identifier,
- the user task,
- optional business context,
- optional CRM record summaries,
- attack metadata when applicable,
- and answer-format requirements.

Example high-level shape:

```json
{
  "track": "crmarena",
  "scenario": "saleforceonespy",
  "task": "Provide a churn analysis for this account.",
  "crm_context": "...",
  "answer_format": "structured"
}
```

---

## Output contract

A normalized result should capture at least:

- final answer,
- usefulness summary,
- leakage flags,
- scenario outcome,
- and evaluation metadata.

Example high-level shape:

```json
{
  "track": "crmarena",
  "scenario": "saleforceonespy",
  "final_answer": "Customer risk is elevated due to declining engagement.",
  "usefulness": "high",
  "internal_logic_leak": false,
  "status": "ok"
}
```

---

## Security expectations

The integration should enforce a simple principle:

> Be useful for CRM work without disclosing proprietary internals.

Therefore the CRMArena track must resist:

- direct requests for formulas,
- requests for system prompts,
- requests for internal tool definitions,
- pattern-completion extraction attempts,
- and indirect attempts to reconstruct proprietary scoring logic.

---

## Local workflow

Recommended workflow inside AegisForge:

1. Run adapter tests.
2. Run benign CRM payload.
3. Run extraction-attempt payload.
4. Compare usefulness and leakage outcomes.
5. Export artifacts.
6. Prepare submission bundle.
7. Validate submission structure.

---

## Why this integration exists

CRMArenaPro on its own is a strong business-process benchmark. AegisForge extends it with a scenario-driven security layer so that performance can be measured not only by business utility, but also by resistance to realistic prompt extraction and intellectual-property leakage attempts.
