# TAU2 quipu_lab Submission Pack — Single File

This single Markdown file combines all TAU2 / quipu_lab submission-support documents into one consolidated artifact.

## Contents

1. [TAU2 Contributing Compliance Checklist](#tau2-contributing-compliance-checklist)
2. [TAU2 Delivery Documentation](#tau2-delivery-documentation)
3. [Final PR / Submission Readiness Checklist](#final-pr--submission-readiness-checklist)
4. [Impact Narrative](#impact-narrative)
5. [Issue Draft](#issue-draft)
6. [Optional Paper Outline](#optional-paper-outline)
7. [PR Description Draft](#pr-description-draft)

---


# 1. TAU2 Contributing Compliance Checklist

_Source file: `TAU2_CONTRIBUTING.md`_


---

# TAU2 Contributing Compliance Checklist for AegisForge / quipu_lab

## Objective
Leave the AegisForge `quipu_lab` work in a state that is easy to upstream as a clean PR to `sierra-research/tau2-bench`.

---

## 1) Contribution type
**Best fit right now:** domain-style contribution with a domain/task catalog expansion and supporting validation/tests.

Why this fit:
- You already have a task catalog with `quipu_lab` as the domain default.
- The work includes tasks, validation helpers, smoke coverage, and base-split execution evidence.
- The contribution is stronger as a benchmark/domain contribution than as a vague “miscellaneous improvement”.

---

## 2) Official contributing requirements mapped to your current state

### A. Open an issue first
**Official expectation:** recommended before starting significant work.

**Current status:** still needs to be done upstream.

**Action to close:**
Open a GitHub issue in your fork or, preferably, in the upstream repo discussion flow if appropriate.

### Suggested issue title
`Proposal: add quipu_lab task catalog expansion and AegisForge purple evaluation support`

### Suggested issue body
```md
## Problem / Goal
I want to contribute a benchmark/domain-style extension centered on `quipu_lab`, together with validation and submission preparation support used in AegisForge Phase 2 Purple work.

## Proposed Solution
Add a structured task catalog with smoke/base split support, validation helpers, tests, and submission-ready local evaluation scripts/evidence.

## Impact
- domain/task coverage
- local validation pipeline
- test surface for task catalog integrity
- submission prep workflow

## Timeline
Initial implementation is already working locally; I am now preparing the upstreamable PR.

## Dependencies / Risks
Need feedback on the preferred landing location and whether this should be framed as a domain contribution, experiment, or benchmark extension.
```

---

### B. Use a descriptive branch name
**Current status:** needs explicit upstream branch naming.

### Recommended branch names
- `domain/quipu_lab/aegisforge-purple-catalog`
- `domain/quipu_lab/task-catalog-expansion`
- `domain/quipu_lab/purple-submission-pipeline`

**Use one focused branch only.**

---

### C. Make clean commits
**Current status:** still needs final cleanup for upstream PR.

### Recommended commit sequence
```text
feat: add quipu_lab task catalog expansion
feat: add quipu_lab catalog validation helpers
test: add quipu_lab adapter and smoke coverage
docs: add quipu_lab contribution and run instructions
```

### Avoid
- `update`
- `wip`
- `fixed stuff`
- giant mixed commits that touch unrelated files

---

### D. Tests must pass and new functionality must be tested
**Current status:** largely satisfied locally.

### Evidence already in your favor
- adapter tests pass
- smoke tests pass
- base split evaluation runs successfully
- submission prepare/validate flow passes
- expanded catalog is now being exercised beyond smoke

### Still needed for strict upstream compliance
Run the upstream project’s style/test flow in the actual PR branch if the contribution is being ported into a tau2-bench fork.

### Final upstream command checklist
```bash
uv sync --extra dev
make check-all
make test
```

If your PR touches only text-mode benchmark logic, these three are the important baseline.

---

### E. Documentation must be updated
**Current status:** this package closes most of that gap.

You should include:
- a contribution README or PR body describing the change
- what was added
- how to run it
- what was validated
- known limitations

The companion file `TAU2_DELIVERY_DOCUMENTATION.md` is intended to cover this.

---

### F. PR title and body should follow upstream style
**Recommended PR title**
```text
feat: add quipu_lab task catalog expansion for AegisForge purple evaluation
```

**Minimum PR body sections**
- Summary
- Changes Made
- Testing
- Documentation
- Checklist

A ready-to-adapt version is included in `TAU2_DELIVERY_DOCUMENTATION.md`.

---

## 3) Domain-contribution readiness check

For a domain-style contribution, upstream will care about the following:

### Required
- [x] tasks exist and are structured
- [x] validation helpers exist
- [x] task IDs are catalogued cleanly
- [x] smoke coverage exists
- [x] base split execution has been exercised locally
- [x] required tools are explicitly declared
- [x] task payload validation is present

### Should still be polished
- [ ] upstream-facing README for the contribution
- [ ] style/lint pass in the actual tau2-bench fork branch
- [ ] clean PR-sized diff instead of a private-repo-only diff
- [ ] explicit issue link in the PR body
- [ ] encoding cleanup for agent card text if that code is part of the PR scope

---

## 4) Exact pre-PR gate
Do not open the upstream PR until all boxes below are true.

- [ ] Issue opened or maintainer feedback requested
- [ ] One clean branch created
- [ ] Commits split by purpose
- [ ] `make check-all` passes in the PR branch
- [ ] `make test` passes in the PR branch
- [ ] PR body filled using the template
- [ ] Documentation file added/updated
- [ ] No unrelated files in the diff
- [ ] Any mojibake/encoding problem either fixed or explicitly declared out of scope

---

## 5) Fast verdict
### What is already strong
Your work already looks credible on the hard parts: task catalog structure, validation, tests, and execution flow.

### What still blocks “full contributing compliance”
The remaining gap is mostly **upstream hygiene**:
- issue
- branch/commit polish
- upstream style checks
- final PR packaging

Once those are closed, this part should be in good shape.


---


# 2. TAU2 Delivery Documentation

_Source file: `TAU2_DELIVERY.md`_


---

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


---


# 3. Final PR / Submission Readiness Checklist

_Source file: `TAU2_FINAL_SUBMISSION_CHECKLIST(1).md`_


---

# Final PR / Submission Readiness Checklist for τ²-Bench

This checklist is tailored to the current `quipu_lab` / AegisForge contribution state.

## A. PR opening readiness
- [ ] Fork updated and branch named clearly
- [ ] Optional issue opened / linked if maintainers prefer issue-first discussion
- [ ] PR title uses `type: brief description`
- [ ] PR description includes:
  - [ ] Summary
  - [ ] Changes Made
  - [ ] Testing
  - [ ] Documentation
  - [ ] Checklist

## B. Repo contributing-guideline alignment
Based on the current `tau2-bench` contributing guide:
- [x] New functionality is tested
- [x] Documentation prepared
- [x] Commit intent can be described clearly
- [ ] Core tests pass locally in the target repo (`make test`)
- [ ] Style / lint checks pass (`make check-all`)
- [ ] Final commit messages are clear and descriptive

## C. Submission-specific readiness
Based on leaderboard submission docs:
- [ ] `submission.json` conforms to schema
- [ ] submission directory name is finalized
- [ ] `manifest.json` is updated if this is a leaderboard-style submission PR
- [ ] trajectory link is included in the PR description
- [ ] framework modifications or task omissions are documented
- [ ] model / paper / repo links added if available

## D. Already completed locally
- [x] `test_tau2_adapter.py` passing
- [x] `test_tau2_task_catalog.py` passing
- [x] `test_tau2_quipu_lab_smoke.py` passing
- [x] `run_tau2_purple_eval.ps1 -TaskSplit base` passing
- [x] `prepare_tau2_purple_submission.ps1` passing
- [x] `validate_tau2_purple_submission.ps1` passing
- [x] `task_catalog_ok: true`
- [x] `health_ok: true`
- [x] `agent_card_ok: true`
- [x] `local_e2e_ok: true`

## E. Remaining high-value cleanup
- [ ] Fix mojibake / UTF-8 presentation in agent card output
- [ ] Decide whether to keep this as one PR or split into:
  - [ ] domain expansion PR
  - [ ] framework hardening PR

## F. Practical definition of “done”
You can honestly say the contribution is **technically ready** once:
- [x] local pipeline is green
- [x] tests are green
- [x] submission package validates locally

You can honestly say the challenge submission is **formally ready** once:
- [ ] PR is opened to the main repo
- [ ] PR body includes trajectory link and impact narrative
- [ ] contributing-guideline checks are completed in the target repo
- [ ] maintainers confirm scope / structure is acceptable

You can honestly say it is **fully completed** only once:
- [ ] the PR is accepted / merged


---


# 4. Impact Narrative

_Source file: `TAU2_IMPACT_NARRATIVE(1).md`_


---

# Impact Narrative — `quipu_lab` Contribution

## One-paragraph version
This contribution expands `quipu_lab` into a more realistic, multi-turn evaluation domain for organization-facing conversational agents. By growing the domain to **33 validated base tasks** and strengthening catalog integrity, smoke coverage, and end-to-end submission checks, the benchmark gains broader pressure on clarification-before-action, policy-vs-preference tradeoffs, wrong-tool penalties, noisy retrieval, multi-step handoffs, memory preservation, and structured planning. The result is a more demanding and practically useful evaluation surface for purple-agent style systems operating under operational and policy constraints.

## Short PR-ready version
This PR improves benchmark coverage by expanding `quipu_lab` to 33 validated base tasks spanning clarification, retrieval under noise, handoffs, wrong-tool penalties, negotiation consistency, memory preservation, and policy-aware action selection. The contribution strengthens the realism of multi-turn evaluation for organization-facing agents and makes the domain more useful for testing tool-use discipline, operational robustness, and decision quality under constraints.

## Expanded version
The main impact of this contribution is not just “more tasks,” but **better evaluation pressure**.

The added `quipu_lab` tasks broaden the benchmark along several important axes:

1. **Clarification before action**
   - Evaluates whether agents gather missing information before acting.
   - Reduces reward for brittle or over-eager action selection.

2. **Policy-aware decision making**
   - Tests whether the agent can balance user requests against organizational constraints.
   - Better reflects real deployment conditions.

3. **Preference vs policy conflicts**
   - Captures cases where user preference alone should not dictate the outcome.
   - Helps distinguish compliant reasoning from shallow accommodation.

4. **Tool-use discipline**
   - Includes wrong-tool penalties and no-hallucinated-tool scenarios.
   - Improves the benchmark’s ability to detect unsafe or sloppy tool behavior.

5. **Retrieval under noise**
   - Adds distractor-heavy and context-rotation style tasks.
   - Makes evaluation less dependent on easy, direct lookup behavior.

6. **Multi-step handoffs and dependencies**
   - Introduces workflows where success depends on ordered reasoning and coordination.
   - Better approximates real operational settings.

7. **Memory preservation across turns**
   - Evaluates whether important state is retained across multi-turn interactions.
   - Important for realistic agent workflows and longer conversations.

8. **Catalog reliability**
   - Stronger metadata normalization, validation, smoke checks, and catalog tests make the contribution easier to trust, reproduce, and integrate.

## Suggested “Why merge this?” line
This contribution improves both **coverage** and **reliability**: it adds realistic multi-turn evaluation cases while also making the `quipu_lab` catalog easier to validate, test, and maintain.


---


# 5. Issue Draft

_Source file: `TAU2_ISSUE_DRAFT(1).md`_


---

# Issue Draft — Add/upgrade `quipu_lab` domain support for τ²-Bench / AgentBeats Phase 2 Purple

## Summary
I would like to contribute an upgraded `quipu_lab` domain integration for τ²-Bench-focused evaluation flows used in AegisForge Agent / AgentBeats Phase 2 Purple.

This contribution expands the task catalog, strengthens catalog validation, adds dedicated catalog tests, hardens smoke coverage, and includes reproducible local evaluation / submission-prep / validation scripts for the `quipu_lab` pipeline.

## What this adds
- Expanded `quipu_lab` task catalog to **33 base tasks**
- Stronger catalog validation in `tasks.py`
- Dedicated catalog test coverage
- Hardened smoke subset checks
- End-to-end local pipeline for:
  - eval
  - submission package preparation
  - submission validation

## Why this may be useful
This contribution is aimed at improving realistic multi-turn conversational evaluation coverage for organization-facing agent behavior, especially:
- clarification-before-action behavior
- policy vs preference tradeoffs
- tool-use discipline
- multi-step handoffs
- retrieval under noise / distractors
- minimum-query behavior
- memory preservation across turns

## Validation status
Locally verified:
- adapter tests passing
- task catalog tests passing
- quipu_lab smoke tests passing
- Docker build passing
- health check passing
- agent card check passing
- local e2e passing
- submission prepare / validate passing

## Question for maintainers
Would you prefer this contribution as:
1. a domain-focused PR centered on `quipu_lab`, or
2. a broader PR that also highlights the framework hardening around catalog validation and submission scripts?

## Additional notes
I can provide:
- trajectory link in the PR description
- a concise technical note / paper link if useful
- documentation for any framework modifications or task omissions


---


# 6. Optional Paper Outline

_Source file: `TAU2_OPTIONAL_PAPER.md`_


---

# Optional Paper Outline for AegisForge / quipu_lab

This paper is not required, but it can strengthen the submission by explaining the contribution clearly and making the work look more research-grade.

---

## Suggested title options
1. **AegisForge-quipu_lab: Expanding Structured Purple-Agent Evaluation with Multi-Family Task Catalogs**
2. **From Smoke to Catalog: Extending quipu_lab for Broader Purple-Agent Evaluation**
3. **A Structured Task-Catalog Extension for AegisForge Phase 2 Purple Evaluation**

---

## 1) Abstract draft
```text
We present an extension of the `quipu_lab` evaluation setup used in AegisForge Phase 2 Purple work. The contribution expands a previously narrow smoke-oriented path into a broader structured task catalog spanning multiple task families, including planning, retrieval, clarification, negotiation, and hallucination-resistance behaviors. In addition to task expansion, we add catalog validation helpers, preserve smoke/base split behavior, and demonstrate compatibility with a local purple-evaluation pipeline that supports testing, run preparation, and submission validation. The resulting setup improves task diversity, execution realism, and reproducibility while remaining compatible with submission-oriented evaluation workflows.
```

---

## 2) Recommended section structure

### 1. Introduction
Explain:
- what problem you are solving
- why smoke-only coverage is not enough
- why structured task diversity matters for Purple-style evaluation

### 2. Background and Motivation
Cover:
- τ²/τ³-bench style evaluation motivation
- AgentBeats / Purple context
- why local submission pipelines need both task realism and validation discipline

### 3. Contribution Overview
State clearly:
- expanded `quipu_lab` task catalog
- validation helpers
- smoke/base split handling
- local run/prepare/validate support

### 4. Task Design
Describe the task families:
- MCU-like chained planning
- OfficeQA-like extraction and normalization
- CRM-like retrieval under schema drift / recovery pressure
- FWA-like handoff and exception resolution
- bargaining / strategic response tasks
- policy-sensitive customer service tasks
- CAR-style ambiguity and hallucination-resistance tasks

### 5. Implementation
Explain:
- task structure
- metadata design
- required tools
- validation helpers
- base vs smoke split behavior

### 6. Evaluation Workflow
Show:
- adapter tests
- smoke tests
- base split execution
- submission prepare / validate flow
- evidence artifacts produced locally

### 7. Observations and Limitations
Include:
- strengths of the contribution
- remaining encoding issue in agent card
- current evidence is local, not yet maintainer-merged upstream
- future improvements such as richer metrics or more adversarial tasks

### 8. Conclusion
Summarize the value:
- better task coverage
- stronger submission readiness
- more credible Purple-agent evaluation

---

## 3) Figures and tables you should include
### Figure ideas
- pipeline diagram: tests → eval → prepare → validate
- task-family map for the 33-task catalog
- split diagram showing smoke vs base

### Table ideas
- table of task families and counts
- table of supported tools and which task families use them
- table of local validation stages and pass/fail outcome

---

## 4) Claims that are safe to make
- the task catalog was expanded
- smoke and base flows are both supported
- local evaluation/prepare/validate passed
- the contribution improves breadth and packaging discipline

## 5) Claims you should avoid unless you measure them
- “state of the art”
- “significantly better than all prior methods”
- “robust” without reporting failure cases
- “generalizes broadly” without external evidence

---

## 6) Minimal submission version
If you do not want a full paper, write a 2–4 page technical note with:
- abstract
- contribution overview
- task design summary
- evaluation workflow
- limitations

That is already enough to make the work look more complete and intentional.


---


# 7. PR Description Draft

_Source file: `TAU2_PR_DESCRIPTION_DRAFT(1).md`_


---

# PR Draft — feat: add expanded quipu_lab task catalog and hardened tau2 purple validation flow

## Summary
This PR adds and validates an expanded `quipu_lab` contribution for τ²-Bench-oriented evaluation in the AegisForge Agent / AgentBeats Phase 2 Purple workflow.

The contribution focuses primarily on **new or upgraded domains** by expanding `quipu_lab` into a broader, more realistic multi-turn evaluation catalog, and secondarily improves the surrounding evaluation workflow through stronger catalog validation and submission-prep checks.

## Changes Made
- Expanded `quipu_lab` to **33 base tasks**
- Added task coverage across:
  - MCU-style chained / wrong-tool / resource-pipeline / hybrid flows
  - OfficeQA extraction / normalization / ratio and calculation tasks
  - CRM retrieval, recovery, and no-hallucinated-tool scenarios
  - FWA handoff / warehouse / cross-station dependency scenarios
  - negotiation / rebuttal / fairness-vs-welfare tradeoff scenarios
  - telecom / service / memory-preservation scenarios
  - car-policy / preference-conflict / clarify-or-act scenarios
- Hardened `tasks.py` metadata normalization and catalog validation
- Added dedicated task-catalog tests
- Strengthened smoke-subset assertions
- Hardened PowerShell scripts for:
  - local eval
  - submission package preparation
  - submission package validation
- Added stricter checks for:
  - `local_e2e_ok`
  - smoke subset consistency
  - metadata completeness
  - required tool integrity
  - catalog export integrity

## Impact
This PR increases coverage of realistic, organization-facing, multi-turn evaluation behavior in `quipu_lab`.

In particular, it adds stronger benchmark pressure on:
- clarification before acting
- policy-aware decision making
- preference vs policy conflict resolution
- multi-step handoff reliability
- constrained retrieval under noise and distractors
- tool-use discipline and wrong-tool penalties
- memory preservation across turns
- structured planning under operational constraints

This makes the benchmark more useful for evaluating purple-agent style systems that must balance utility, policy, tool correctness, and multi-turn consistency in realistic workflows.

## Testing
Local validation completed successfully:

- `python -m pytest tests\test_adapters\test_tau2_adapter.py -q`
- `python -m pytest tests\test_adapters\test_tau2_task_catalog.py -q`
- `python -m pytest tests\tests_envs\test_tau2_quipu_lab_smoke.py -q`
- `powershell -ExecutionPolicy Bypass -File .\scripts\run_tau2_purple_eval.ps1 -TaskSplit base`
- `powershell -ExecutionPolicy Bypass -File .\scripts\prepare_tau2_purple_submission.ps1`
- `powershell -ExecutionPolicy Bypass -File .\scripts\validate_tau2_purple_submission.ps1`

Observed local validation result:
- `task_count: 33`
- `task_catalog_ok: true`
- `adapter_tests_ok: true`
- `smoke_tests_ok: true`
- `docker_build_ok: true`
- `health_ok: true`
- `agent_card_ok: true`
- `local_e2e_ok: true`

## Documentation
Included / prepared:
- contribution summary
- testing summary
- impact narrative
- pre-submission checklist
- issue draft
- optional paper outline / technical note scaffold

## Limitations / Known Issues
- Agent card text still shows some UTF-8 / mojibake presentation artifacts in local output.
- The local pipeline is green, but final acceptance still depends on maintainer review and integration fit with the main repository.
- If maintainers prefer narrower scope, this may need to be split into:
  - domain contribution
  - framework hardening contribution

## Trajectories
Trajectory files are available and can be linked here for maintainer review:
- [ADD TRAJECTORY LINK HERE]

## References
- [ADD REPO / MODEL / PAPER LINKS HERE]

## Checklist
- [x] Tests pass locally
- [ ] `make check-all` passes in the target repo
- [x] New functionality is tested
- [x] Documentation is updated
- [x] Breaking changes noted (none expected)
- [x] Submission package validates locally
- [ ] `submission.json` and manifest updates are aligned with maintainer expectations
- [ ] Trajectory link added to PR description


---
