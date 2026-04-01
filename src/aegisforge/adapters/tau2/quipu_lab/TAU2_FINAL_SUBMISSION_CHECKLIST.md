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
