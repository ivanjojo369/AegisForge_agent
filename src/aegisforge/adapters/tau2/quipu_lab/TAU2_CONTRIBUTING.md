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
