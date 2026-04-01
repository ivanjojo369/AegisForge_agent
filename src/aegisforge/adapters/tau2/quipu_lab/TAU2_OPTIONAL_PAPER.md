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
