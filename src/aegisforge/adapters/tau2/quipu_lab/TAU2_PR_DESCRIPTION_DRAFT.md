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
