# Nebius × NVIDIA Global AI Hackathon 2026 — Evaluation & Reproduction Guide

> **Track:** Coding and Agentic Engineering  
> **Submitted project:** AegisForge Nexus — Colossus  
> **Purpose of this file:** Help judges understand the system, run it safely, reproduce the central demonstration, evaluate the evidence, and distinguish verified capabilities from roadmap items.  
> **Language:** English (the hackathon requires English submission materials or English translations).  
> **Last prepared:** October 29–30, 2026.

---

## 0. Quick-start for judges: read this first

**What is it?** AegisForge Nexus is a locally operated, evidence-driven coding-agent prototype. It integrates an NVIDIA Nemotron model served through **Nebius Token Factory** to propose source-code repairs, lets a human review changes, and compares the original and proposed code under the **same regression test** in isolated Docker execution. Results, patch hashes and run history are recorded by the platform.

**Why it matters:** The model does not get to declare its own answer correct. A proposed patch must be reviewed and tested. A failed first proposal can be followed by a second, separately authorized proposal, with both rounds preserved.

**The shortest route to evaluate it:**

1. Watch the public demo video linked from the Devpost submission (**the entrant must insert the final URL**).
2. Inspect the two real reported outcomes in [§6](#6-reported-demonstration-results): a Nemotron-generated repair and a second-file repair verified using an operator-supplied regression.
3. Review the source files identified in [§3](#3-where-to-look-in-the-source-code).
4. If running locally, follow [§4](#4-installation-and-local-launch), then [§5](#5-guided-live-demonstrations).
5. Interpret the verification fields using [§7](#7-how-to-interpret-the-evidence).

**Important:** `http://127.0.0.1:8012/` is only reachable on the computer running the application. It is **not** a public judge-accessible demo. The Devpost submission must separately include a functioning hosted demo or downloadable test build with a working evaluation path; this Markdown guide alone is not a substitute.

### Evaluation links (entrant must finish before submission)

| Resource | Link/status |
|---|---|
| Hackathon entry | https://nebiusglobalaihackathon.devpost.com/submissions/1193685-aegisforge-nexus |
| Public repository | https://github.com/ivanjojo369/AegisForge_agent |
| Public YouTube demo, ≤3 minutes | **TO BE ADDED — required** |
| Working hosted demo or reproducible test build | **TO BE ADDED — required** |
| Released version/commit SHA | **TO BE ADDED — required for reproducibility** |
| Test image availability/build instructions | **TO BE VERIFIED — see §4.4** |

> **Release warning:** The public repository must be synchronized with the actual tested Nexus implementation before the links above can be considered representative. Older versions of this repository describe a different AgentX/AgentBeats project in the root README. Do not judge the Nexus submission solely from those older files.

---

## 1. Problem, approach and track fit

### 1.1 Problem

An AI coding model can propose a plausible diff and still introduce errors or fail to resolve the original defect. Traditional coding demonstrations often stop after showing a generated patch. This system adds a verification boundary: the original must fail the specified regression and the candidate must pass it, with both outcomes recorded.

### 1.2 Six-stage workflow

1. **Understand:** Select a repository and read a specifically authorized file.
2. **Plan:** Describe a concrete repair objective; use observed test failures as feedback.
3. **Propose:** Ask NVIDIA Nemotron via Nebius Token Factory to propose a change, or import a complete unified diff for deterministic offline evaluation.
4. **Check:** Validate path allowlists, source snapshots, diffs, edit structure, syntactic constraints where applicable, and human review.
5. **Verify:** Execute baseline and candidate against a matching regression inside isolated Docker copies; retain summary evidence and hashes.
6. **Iterate:** If the candidate fails, prepare a revised objective and explicitly authorize any subsequent model call.

The displayed six-stage lifecycle is the intended engineering workflow. Individual stages have been demonstrated in controlled cases; fully autonomous reasoning across arbitrary repositories and file formats is **not** claimed.

### 1.3 Nebius and NVIDIA are core to the product

- **Service:** Nebius Token Factory inference API.
- **Model used in the demonstrated proposal flow:** `nvidia/Nemotron-3_5-Lightning`, as reported by the local application during testing. Availability can change; the operator must check the available model catalog.
- **Role of the model:** Read bounded, specifically authorized source context and propose code edits. The model does **not** directly mutate the working repository or get automatic permission to execute its own code.
- **Where Nebius fits:** Remote inference for the code proposal. Validation, Trust approval, Docker execution and ProofGraph recording are handled by the local engineering application.
- **Inference policy:** Explicit opt-in; a first proposal and an independently authorized post-failure revision under the current demonstration flow. Provider calls may incur usage charges. A failed response is not proof of zero billing.

### 1.4 Intended users

Developers, maintainers, research engineers and coding-agent evaluators who want a reviewable chain from AI suggestion to regression evidence, rather than an unverified auto-apply workflow.

---

## 2. Architecture at a glance

```text
Browser: Nexus / Colossus console (local)
   |
   | Select source, define objective, inspect proposal, approve effects
   v
scripts/nexus/serve_nexus_web.py
   |
   +--> Repository inventory / authorized read-only source snapshot
   |      src/aegisforge/nexus/workspace/
   |
   +--> NVIDIA Nemotron proposal via Nebius Token Factory (optional)
   |      src/aegisforge/nexus/nebius_client.py
   |      src/aegisforge/nexus/engineering/code_proposer.py
   |
   +--> Unified-diff and source/hash checks
   |      src/aegisforge/nexus/engineering/diff_builder.py
   |
   +--> Human approval / Trust policy
   |      src/aegisforge/nexus/trust/
   |
   +--> Docker: baseline copy + candidate copy + same pytest regression
   |      scripts/nexus/demo_nexus_engineering_host_offline.py
   |      src/aegisforge/nexus/mission_control/mission_engineering_host.py
   |
   +--> Result summaries, round history and ProofGraph digests
          src/aegisforge/nexus/proof/
```

### Security model and boundaries

- Repository inspection begins with an operator-selected directory and remains read-only at the live source.
- Model proposals are **proposals**, not direct instructions to the executor.
- Source/hash binding helps detect stale or mismatched patches.
- Executing code and other effects require a fresh, human-reviewed Trust approval; no silent blanket authorization is intended.
- Docker tests operate on generated copies; reported `original_untouched: true` means the source repository was not changed by that verification run.
- The test container is designed to run without network access and with resource limits. **Docker is defense in depth, not an absolute security boundary for hostile code.** Use a dedicated VM for genuinely untrusted submissions.
- Do not provide production secrets, cloud credentials, SSH keys, `.env` contents, private repositories or Docker socket access to a test container.
- History stored only in process memory can disappear on restart. Export the relevant evidence before restarting.
- A SHA-256 proof graph is evidence of recorded content consistency; it is **not**, by itself, a third-party attestation of the provider or patch authorship.

---

## 3. Where to look in the source code

The following paths are present in the developer's most recent local source snapshot and are the expected layout of the release. Confirm them in the published commit:

| Area | Path | Purpose |
|---|---|---|
| Local web server | `scripts/nexus/serve_nexus_web.py` | Local UI, session authorization, repository reading, model requests, review and Docker endpoints |
| Web interface | `src/aegisforge/web/nexus/index.html` | Human-facing forms and review panels |
| Browser logic | `src/aegisforge/web/nexus/app.js` | Proposals, diff import, reviewer interactions, status display |
| Browser styling | `src/aegisforge/web/nexus/styles.css` | Visual design |
| Nebius client | `src/aegisforge/nexus/nebius_client.py` | Nebius model integration |
| Model proposal validation | `src/aegisforge/nexus/engineering/code_proposer.py` | Structured edits and strict checks |
| Diff builder | `src/aegisforge/nexus/engineering/diff_builder.py` | Source-bound patch construction/review |
| Engineering host | `src/aegisforge/nexus/mission_control/mission_engineering_host.py` | Supervised engineering execution |
| Sandbox and approvals | `src/aegisforge/nexus/trust/` | Approval policy and isolated execution mechanisms |
| Evidence graph | `src/aegisforge/nexus/proof/` | Proof and provenance records |
| Repository indexing | `src/aegisforge/nexus/workspace/repo_index.py` | Authorized file inventory |
| Offline/Docker demonstration | `scripts/nexus/demo_nexus_engineering_host_offline.py` | Reproducible verification entry points |
| Example file A | `scripts/build_scorecard.py` | `warnings` regression scenario |
| Example file B | `scripts/prepare_submission.py` | URL normalization regression scenario |

**Scope:** The same console also shows AegisGrid and Live Forge workspaces. Those workspaces represent related planned domains; the **Coding and Agentic Engineering** submission should be judged primarily on the Nexus Engineering workflow described here. Do not infer production-ready energy control or Twitch live integrations from navigation cards alone.

---

## 4. Installation and local launch

### 4.1 Prerequisites

- A **Windows 10/11** development machine is the known tested operator configuration; Linux/macOS may need adaptation.
- **Python 3.11+** and `pip`; a virtual environment is recommended.
- **Docker Desktop**, running a Linux engine, for the verified-repair demonstration.
- A complete checkout containing the Nexus files above (not merely the older public branch).
- Optional: a valid **Nebius Token Factory API key** for live Nemotron proposals. Offline diff review must not require a model call.
- A **locally available immutable digest-pinned** `colossus-pytest` Docker image containing Python and pytest. Image portability is an outstanding release prerequisite; see §4.4.

### 4.2 Clone and install (PowerShell)

From any directory where you are authorized to create a project folder:

```powershell
# Clone the release containing the Nexus implementation.
git clone https://github.com/ivanjojo369/AegisForge_agent.git
cd .\AegisForge_agent

# Use Python 3.11 or later.
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

**Before running:** Check that `scripts/nexus/serve_nexus_web.py` exists in the cloned branch. If missing, the published repository has not yet been synchronized and this guide cannot make the old branch executable as Nexus.

To check the available Python tests (may be substantial):

```powershell
python -m pytest -q --import-mode=importlib --tb=short -x
```

`--import-mode=importlib` avoids known test-module-name collisions in this repository. Do not interpret a successful test suite as proof that an arbitrary Nemotron patch is correct.

### 4.3 Optional: secure API-key setup (PowerShell)

**Only needed for a live model proposal.** Enter the key in the **same PowerShell session** that will launch the server:

```powershell
$secureKey = Read-Host "Enter NEBIUS_API_KEY" -AsSecureString
$env:NEBIUS_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
Remove-Variable secureKey
-not [string]::IsNullOrWhiteSpace($env:NEBIUS_API_KEY)
```

The final command should print `True`. Never display the actual key, commit it to the repository, embed it in the frontend, place it in screenshots, or share it with evaluators. This session-local environment variable is not permanently stored in Windows by these commands.

Judges should **not** be expected to provide their own paid credentials. The entrant must supply a lawful, secure and free evaluation route that meets the hackathon's working-demo requirement.

### 4.4 Docker image: essential reproducibility check

The developer's successful demonstrations used a local image reference:

```text
colossus-pytest@sha256:8ca4170b79f36d99efb3d5d82a16ffc3fc4fbf6fdac03d965c2fb2506b649d38
```

**This is an environment-specific observed reference, not a guarantee that other computers can pull or inspect the image.** On a new evaluation computer, test:

```powershell
docker version
docker image inspect "colossus-pytest@sha256:8ca4170b79f36d99efb3d5d82a16ffc3fc4fbf6fdac03d965c2fb2506b649d38"
```

If inspection fails, do **not** claim Docker is ready. The release must provide one of the following before judging:

- A publicly accessible, immutable image and an exact pull/reference instruction; **or**
- A Dockerfile with its **actual** `nexus_pytest_sandbox` build stage plus a documented process for obtaining a reference accepted by the server; **or**
- A distributed test build that already contains a verifiably reproducible equivalent environment.

The operator previously used a customized Dockerfile stage called `nexus_pytest_sandbox`. That stage was not present in the older source snapshot available during preparation of this guide. **Confirm and publish it before relying on image-build instructions.** An arbitrary `:latest` image tag is not interchangeable with a digest-pinned image in the current runner.

### 4.5 Start the local interface — no inference mode

Use this path first if you only want to import a pre-existing diff and run tests, and the pinned image exists locally:

```powershell
python scripts/nexus/serve_nexus_web.py --verified-demo `
  --demo-image "colossus-pytest@sha256:8ca4170b79f36d99efb3d5d82a16ffc3fc4fbf6fdac03d965c2fb2506b649d38" `
  --inspect-repo "$((Get-Location).Path)"
```

The server should print its address and a **one-session local demonstration token**. Open:

```text
http://127.0.0.1:8012/
```

Paste the token into the dashboard's **“Token de demostración”** password field. The UI is currently in Spanish; this guide maps each important Spanish label to English below. The token is local and should **not** appear in the Devpost description or video.

### 4.6 Start with optional Nebius proposals

After the key is set and the image is available, stop the previous server (`Ctrl+C`) and run:

```powershell
python scripts/nexus/serve_nexus_web.py --verified-demo `
  --demo-image "colossus-pytest@sha256:8ca4170b79f36d99efb3d5d82a16ffc3fc4fbf6fdac03d965c2fb2506b649d38" `
  --inspect-repo "$((Get-Location).Path)" `
  --allow-nebius-proposals
```

Every Nebius proposal request requires a human decision; inference can have a cost. Do not repeatedly retry errors such as HTTP 502 automatically. The optional model check verifies catalog access without running an inference; a successful catalog lookup is **not** the same as a successful code-generation call.

### 4.7 If the Docker image is unavailable

The read-only/offline dashboard can still be opened without the Docker-demo flags:

```powershell
python scripts/nexus/serve_nexus_web.py
```

This is useful for reviewing UI and imported evidence, but **does not demonstrate a successful live Docker repair**. Explain this limitation to the evaluator; do not present a disabled verification button as an implementation failure or a successful repair.

---

## 5. Guided live demonstrations

**Safety first:** Perform these steps in a disposable test clone or other repository you own. Review all diffs and pytest code before approving execution. The original source files should remain unchanged during the verification run.

### 5.1 Interface translation reference

| Visible Spanish UI label | Meaning |
|---|---|
| **Repositorio autorizado** | Authorized repository inventory |
| **Leer y analizar archivo (local)** | Read the selected file locally |
| **Comprobar acceso al modelo (sin inferencia)** | Check model catalog without inference |
| **Solicitar propuesta a Nemotron (requiere autorización)** | Request an opt-in Nemotron proposal |
| **Reutilizar un diff anterior (sin consulta a Nebius)** | Paste a previously prepared unified diff; no model call |
| **Prueba de regresión para Docker** | Paste a reviewer-supplied pytest regression |
| **Revisar diff pegado (sin Nebius)** | Validate and review the imported diff |
| **Verificar este diff en Docker (copia aislada)** | Run baseline/candidate checks using isolated copies |
| **Autorizar este efecto** | Approve this specific Trust effect only |
| **Preparar segunda ronda desde el fallo Docker (sin Nebius)** | Prepare a second-round objective from a failed run |

### 5.2 Recommended judge demo A — verify a *second* repository file

This route is deterministic, does not require paid inference, and demonstrates that the Docker verification path is no longer restricted to a single hard-coded source file.

1. Launch the interface with `--verified-demo`, `--inspect-repo` and the pinned image.
2. Enter the local session token.
3. In the repository inventory choose `scripts/prepare_submission.py`.
4. Click **“Leer y analizar archivo (local)”**.
5. In the **“Reutilizar un diff anterior”** box, paste the *complete* unified diff below. Do not include triple-backtick Markdown fences when pasting.
6. In the **“Prueba de regresión para Docker”** box paste the pytest test below.
7. Click **“Revisar diff pegado (sin Nebius)”** and inspect the accepted change.
8. Click **“Verificar este diff en Docker (copia aislada)”**.
9. Review and individually approve every Trust effect shown. Do not approve unexpected effects.
10. Inspect the JSON outcome. The expected result is baseline failed, candidate passed, and `verified_repair: true` **only for the matching supplied test**.

**Patch:**

```diff
--- a/scripts/prepare_submission.py
+++ b/scripts/prepare_submission.py
@@ -51,7 +51,7 @@
 
 
 def normalize_url(url: str) -> str:
-    return url.rstrip("/")
+    return url.strip().rstrip("/")
 
 
 def validate_payload(payload: dict) -> None:
```

**Regression test:**

```python
from pathlib import Path
import runpy


def test_normalize_url_strips_whitespace():
    target = Path(__file__).parent / "scripts" / "prepare_submission.py"
    normalize_url = runpy.run_path(str(target))["normalize_url"]
    assert normalize_url("  https://example.org/demo/  ") == "https://example.org/demo"
```

**Why this test is meaningful:** The original `rstrip("/")` does not remove leading spaces, so the baseline fails this exact assertion. The candidate's `strip().rstrip("/")` removes surrounding spaces and the trailing slash, so the candidate passes.

**If it fails to import:** The patch is **source-version-dependent**. Verify that the selected and read file is exactly `scripts/prepare_submission.py`, that the unchanged context matches the published revision, and that the hunk headers are intact. Do not change file paths arbitrarily or bypass the SHA-256 checks.

**Expected scope:** A passing result proves this regression for the checked snapshot, not all URL-processing behavior and not the entire repository.

### 5.3 Recommended judge demo B — genuine NVIDIA Nemotron proposal

This scenario best illustrates the competition's essential Nebius × NVIDIA integration. It needs the operator's securely configured API key and the operator's explicit authorization.

1. Start in the `--allow-nebius-proposals` mode (see §4.6).
2. Select `scripts/build_scorecard.py` in the authorized inventory and read it locally.
3. Set a specific objective, for example:

   > Harden `warnings` processing so null values, unexpected scalar values, and mixed lists cannot crash scorecard generation, while retaining warning-count and fallback behavior. Return strictly reviewable edits.

4. Check model availability using the catalog-check button if useful. This does not generate a patch.
5. Click **“Solicitar propuesta a Nemotron (requiere autorización)”**. Review the stated source-sharing and possible-cost implications before proceeding.
6. The UI should display the model proposal's diff and metadata. Review its path and content; do not assume it is correct merely because the model generated it.
7. If available, choose **“Verificar este diff en Docker (copia aislada)”** and review every Trust approval.
8. Compare baseline/candidate and examine the JSON evidence and any limitations.
9. **Only if the candidate fails** and the application permits it, click **“Preparar segunda ronda desde el fallo Docker (sin Nebius)”**. Review the revised prompt and separately authorize a second inference if you choose.
10. Inspect `round_history` to confirm whether both attempts are retained.

**Note on negative tests:** If the regression includes `warnings=42`, some earlier Nemotron responses that passed a narrower test may fail the expanded regression. That is correct behavior: a more comprehensive test can reveal an incomplete repair. Do not soften the regression merely to obtain `verified_repair: true`.

**Note on provider failures:** The project previously encountered `proposal_schema_rejected`, `proposal_syntax_invalid`, and `proposal_edit_count_invalid` during development. These reflect local proposal-validation decisions and do not by themselves prove a successful inference or a valid patch. A later valid proposal was received and verified under a matching regression. No result should be fabricated to hide these earlier failures.

### 5.4 Full regression suite (optional)

On an isolated developer checkout:

```powershell
python -m pytest -q --import-mode=importlib --tb=short -x
```

This is broader project-test coverage. It is **not** a substitute for baseline-vs-candidate matching regression evidence.

### 5.5 What an independent reviewer should check

- The source path in the UI, diff and JSON all match.
- The original snapshot hash is stable during the operation.
- The candidate patch has a *different* patchset hash than the rejected first attempt when iterating.
- The Docker run is not synthetic when claiming to repair an actual repository file (`synthetic: false`, `source_snapshot: true`).
- `baseline` and `candidate` reflect a meaningful, *matching* test.
- The original repository is untouched.
- The historical entries are distinct and their digests are available.
- Nebius origin is claimed only when the app reports `proposal_origin: nvidia_via_nebius`; imported patches are clearly labeled otherwise.
- If `authenticity_verified` is `false`, do not claim independent provider attestation.
- No unrestricted shell commands, network calls, auto-approvals or secret exposure were introduced.

---

## 6. Reported demonstration results

These are **recorded outcomes reported from the entrant's Windows/Docker environment**. They are useful evidence summaries, not an independent cryptographic attestation or a fresh evaluation run by the reader.

### 6.1 Model-assisted code repair with a preserved failure-and-success history

**File:** `scripts/build_scorecard.py`  
**Objective:** Fix a `warnings`-handling regression.  
**Model used for the successful proposal:** NVIDIA Nemotron via Nebius Token Factory, according to the application-reported proposal origin.

| Field | Observed value |
|---|---|
| `mode` | `local_docker_verified_repair` |
| `target_path` | `scripts/build_scorecard.py` |
| `synthetic` | `false` |
| `source_snapshot` | `true` |
| `baseline` | `sandbox_failed` |
| `candidate` | `sandbox_passed` |
| `verified_repair` | `true` |
| `proposal_origin` | `nvidia_via_nebius` |
| `verification_scope` | `matching_sandbox_test_only` |
| `original_untouched` | `true` |
| `round_history` | 2 entries: one failed imported proposal, one passing Nebius-attributed proposal |
| `authenticity_verified` | `false` |

**Candidate patchset SHA-256:**

```text
193d127b817048b96317fdfaec75ee6006c848a887b675343c2f8cfeb5d783fc
```

**Earlier failed patchset SHA-256:**

```text
0e4a08d364c4cb102bcae1d98ffe831f1bf0b8849d873143f985c35d8b12945b
```

**Passing round ProofGraph SHA-256:**

```text
b368863fdf04518a8a70da62383664823a6a5792da6f7a23f5364a5d5072c2a4
```

**Interpretation:** The selected regression failed on the original and passed on the candidate; two distinct rounds were retained. This does **not** establish that every edge case of `warnings` was resolved or that Nebius independently attested the proposal's authorship. The later expanded regression should be run before any broader assertion.

### 6.2 Generalized source-file verification with an operator-supplied test

**File:** `scripts/prepare_submission.py`  
**Objective:** Normalize whitespace around a URL before removing trailing slashes.  
**Proposal source:** Imported locally, with no new Nebius inference in that verification run.

| Field | Observed value |
|---|---|
| `mode` | `local_docker_verified_repair` |
| `target_path` | `scripts/prepare_submission.py` |
| `synthetic` | `false` |
| `source_snapshot` | `true` |
| `baseline` | `sandbox_failed` |
| `candidate` | `sandbox_passed` |
| `verified_repair` | `true` |
| `proposal_origin` | `user_imported_unverified` |
| `nebius_calls` | `0` |
| `verification_scope` | `operator_supplied_docker_regression_only` |
| `original_untouched` | `true` |
| `authenticity_verified` | `false` |

**Patchset SHA-256:**

```text
ad21c1b8df309d8ef327200a019fc91c893969f07d78aa5b33b0be88e2265062
```

**Regression SHA-256:**

```text
c6b50d12195033dfaf100525053c063c2841f6274c9eeca4de45ff139101d5de
```

**ProofGraph SHA-256:**

```text
3800a871e5f1f4672bee01f2feac90e2cbe7a960b9f5b16200fb0ff0a86209dd
```

**Interpretation:** A second file type within the Python source tree was checked through a general regression route instead of the original scorecard-only path. This run demonstrates the verifier's *extensibility*, not additional Nebius usage.

---

## 7. How to interpret the evidence

The most important JSON fields:

| Field | Meaning | Caveat |
|---|---|---|
| `mode` | Execution/verification flow | Inspect the actual mode, not just UI labels |
| `synthetic` | Whether a synthetic fixture is used | For a real source snapshot, expect `false` |
| `source_snapshot` | Whether a real authorized source was copied for testing | Does not mean the original was edited |
| `target_path` | Relative file tested | Must match diff headers and selected source |
| `base_sha256` | Hash of source text | Protects against accidental source mismatch |
| `patchset_sha256` | Digest of reviewed patchset | Different proposed changes should have different hashes |
| `regression_test_sha256` | Digest of matching regression | A test hash alone does not establish test quality |
| `proposal_origin` | Recorded origin of proposal | `nvidia_via_nebius` is an app assertion, not provider attestation |
| `nebius_calls` | Provider calls accounted for in that particular verification response | `0` in Docker verification does **not** mean no earlier proposal inference took place |
| `baseline` | Result on original snapshot | A failing baseline is expected for a valid regression demonstration |
| `candidate` | Result on patched copy | Passing means only the executed checks passed |
| `verified_repair` | Whether the configured verification criterion passed | Scope depends on the regression and its meaningfulness |
| `verification_scope` | What the result actually covers | Read it before making any broad quality claim |
| `proof_graph_sha256` | Recorded proof graph digest | Not independent provenance authentication |
| `round_history` | List of retained attempts | Verify count, origins, hashes and pass/fail transitions |
| `original_untouched` | Live original was not modified by verification | A separate deployment/apply step would need its own review |
| `human_review_required` | Further operator review remains necessary | `true` is expected and is not a failed result |
| `authenticity_verified` | Independent origin verification status | `false` forbids claims of authenticated provenance |

### Expected verification pattern

```json
{
  "baseline": "sandbox_failed",
  "candidate": "sandbox_passed",
  "verified_repair": true,
  "original_untouched": true,
  "human_review_required": true
}
```

This is an **illustrative subset of expected fields**. It is not a fabricated full execution record. A legitimate rejection (`candidate: sandbox_failed`, `verified_repair: false`) is useful negative evidence: the system did not falsely certify the patch.

---

## 8. Known limitations and non-claims

1. **Local-only UI.** The current server binds to `127.0.0.1`. Running it does not automatically create a judge-accessible URL.
2. **Evaluation access is a separate deliverable.** A working demo/test build, its instructions and a public YouTube video must be supplied in Devpost. Do not publish development session tokens or live Nebius secrets to meet this requirement.
3. **Source limitations.** Text files in the authorized repository inventory can be reviewed, but the current implementation is not a universal binary-patch and binary-behavior verifier.
4. **Verification coverage is conditional.** Operator-supplied pytest can cover a second source file. Verifying an arbitrary format still requires a meaningful test, and a passing test does not guarantee correctness beyond that scope.
5. **Single-file demonstration focus.** Cross-file refactors, full dependency graph reasoning and project-level migrations have not been demonstrated with the same level of evidence.
6. **Assisted iteration.** The user supplies goals, reviews proposals and approves effects. It is inaccurate to claim fully unattended end-to-end autonomy.
7. **Temporary history.** In-memory review/round state can be lost after server restart; save evidence before restarting.
8. **Provider failures.** Schema and syntax rejection, rate limiting, network failures or ambiguous billing can happen. The app must not automatically retry potentially chargeable inference.
9. **Provenance.** Hashes help with integrity and correlation but do not prove independently that a particular provider produced the patch (`authenticity_verified: false`).
10. **Security boundary.** Docker reduces risks but does not make execution of adversarial code categorically safe. Stronger isolation is needed for truly hostile repositories.
11. **Language.** The current web interface is principally Spanish. Judges can use the bilingual label reference in §5.1; the submission description, this guide, and the demonstration video or English subtitles should be in English.
12. **Docker-image portability.** The observed local digest is not automatically usable on a new computer. Publishing a reproducible image or build is a release blocker for independent Docker reproduction.

---

## 9. Troubleshooting

| Problem | Likely cause | Action |
|---|---|---|
| `--allow-nebius-proposals requires --verified-demo and --inspect-repo` | Missing required startup flags | Start with all three flags in §4.6 |
| `NEBIUS_API_KEY must be set` | Environment variable not present in server's shell | Set it securely in that same terminal; never paste it into logs |
| Port 8012 already in use | Another local server owns the port | Stop the previous server or use `--port` with another local high port |
| Browser does not authenticate | Token from a previous server session | Use the newly printed local demo token |
| The diff belongs to a different file | Selected file does not match `--- a/...` and `+++ b/...` | Select and read exactly that file before importing |
| “Paste the complete unified diff” | Missing headers, extra Markdown fences, wrong target or malformed hunk | Paste a full `---`, `+++`, `@@` diff for the selected source |
| Hash mismatch / HTTP 409 | File changed, newline mismatch, stale read or nonmatching patch | Read source again; generate a diff against the exact current content |
| `proposal_schema_rejected` | Model output did not satisfy the review schema | Inspect validation diagnostic and required edit structure; do not bypass checks |
| `proposal_syntax_invalid` | Rebuilt candidate fails syntax validation | Reject patch; obtain or provide a corrected proposal |
| `proposal_edit_count_invalid` | Edit count violates active validation contract | Use updated reviewed proposer; keep all edits authorized and non-overlapping |
| HTTP 502 after inference | Model/proposal or upstream error, possibly after billable processing | Do not automatically retry; inspect diagnostic and billing separately |
| Docker verification disabled | No valid review, no matching test, missing image, or unavailable demo mode | Supply a valid diff/test and confirm Docker/image startup settings |
| `docker image inspect` fails | Pinned local image absent | Obtain/publish a valid immutable image or rebuild from verified release instructions |
| Baseline passes | Regression does not demonstrate the original bug | Improve test before claiming a verified repair |
| Both baseline and candidate fail | Proposal not shown to fix the tested defect | Review failing test and change, consider a revised round if allowed |
| Candidate passes but scope is narrow | Only selected test passed | Report exact scope; avoid claims of general correctness |
| `round_history` reset after restart | Temporary in-process session lost | Preserve JSON evidence before restarting |

---

## 10. How we evaluate quality and impact

This demonstration is best evaluated against four questions:

1. **Technological implementation:** Was NVIDIA Nemotron used through Nebius Token Factory for a real code proposal? Was the proposal connected to a working review/test flow?
2. **Product design:** Can the operator identify a file, understand the request, inspect a diff, authorize effects and interpret evidence in one console?
3. **Impact:** Does the design reduce the risk of treating an untested model response as a verified fix? Can the reviewer reproduce and reject a bad candidate?
4. **Originality:** Does the product connect model inference, human Trust boundaries, matching baseline/candidate tests and evidence history into a coherent coding-agent experience?

The above corresponds to the public judging themes of technological implementation, design, potential impact and quality of the idea. The project should be judged on its **demonstrated capabilities**, not on aspirations for fully general artificial intelligence.

---

## 11. Significant improvements made for this hackathon

The repository predates this challenge and includes older AegisForge/AgentX code. The hackathon-specific engineering work should be presented as a **substantial development**, not as though the entire prior repository was newly created.

Improvements demonstrated in the local Colossus work include:

- A Nexus browser-based engineering console and local server route for supervised coding workflows.
- NVIDIA Nemotron proposal generation through Nebius Token Factory with explicit user authorization.
- Source inventory, specific-file read, source/hash binding, and review of complete unified diffs.
- Model-output schema and syntax validation to reject unusable proposals instead of applying them.
- Trust approvals for individual execution effects, without silently approving them en masse.
- Isolated Docker baseline/candidate testing with auditable output fields and original-source preservation.
- Iteration history preserving a rejected first proposal and a passing later model-attributed proposal.
- Extending the regression route to another text-source file via operator-provided pytest.
- Distinguishing a passing test from broad correctness, and recorded provider origin from independently authenticated provenance.

**Entrant action:** In the final Devpost description, include only changes actually present in the published commit; identify the original project versus challenge-period development accurately.

---

## 12. Entrant release checklist — complete before submitting

### Code and reproducibility

- [ ] Synchronize the actual tested Nexus code to the public GitHub repository.
- [ ] Verify the server and interface paths in §3 are public and match the tested local code.
- [ ] Update the root `README.md` to point prominently to this evaluation guide and the Nebius × NVIDIA submission.
- [ ] Keep an OSI-compatible license file visible; the repository previously used MIT.
- [ ] Include dependencies, Python version and any required assets.
- [ ] Publish/reproduce the pinned Docker pytest image or a reliable build/test artifact usable by judges.
- [ ] Verify clean-clone installation (not just an environment already configured on the developer's PC).
- [ ] Run relevant tests and the two demonstration scenarios against the exact release commit.
- [ ] Exclude `.env`, API tokens, private keys, user identifiers, browser session tokens, local caches and sensitive traces.

### Judge experience

- [ ] Insert a working public demo/test-build URL in §0 and Devpost.
- [ ] Provide a no-cost judge-accessible path that does not require judges to buy Nebius credits.
- [ ] Ensure the access method does not expose an unrestricted local admin session or approval token.
- [ ] Record a public YouTube demo of **three minutes or less** showing real application behavior and the Nebius/NVIDIA integration.
- [ ] Provide English voice-over, captions or translation for any Spanish UI footage.
- [ ] Show at least one real failure-to-success baseline/candidate verification and explain the limited test scope.
- [ ] Keep evaluation access available through the official judging period.

### Devpost completeness

- [ ] Select **Coding and Agentic Engineering**.
- [ ] Fill in project description, how it was built, challenges, accomplishments and next steps.
- [ ] Provide public repository and working demo/test-build links.
- [ ] Add public YouTube video link (≤3 min).
- [ ] Explain exactly how Nemotron and Nebius Token Factory are used.
- [ ] Provide feedback on the Nebius/NVIDIA tools used.
- [ ] Explain significant new development performed during the submission period.
- [ ] Confirm all materials and testing instructions are in English or translated.
- [ ] Check the official eligibility rules and enter accurate participant information.
- [ ] Review the complete submission and click the actual final submission control before the deadline.

**Official deadline:** Friday, October 30, 2026 at **10:00 a.m. PDT** (11:00 a.m. Guadalajara/Mexico City time).  
**Official reference:** https://nebiusglobalaihackathon.devpost.com/  
**Official rules:** https://nebiusglobalaihackathon.devpost.com/rules

> **Eligibility reminder:** The published rules require individuals to have reached the age of majority where they reside. This guide does not establish any participant's eligibility. Any uncertainty should be resolved with the organizers; do not misrepresent age or team authorization.

---

## 13. Final evaluation summary

**Demonstrated:** NVIDIA Nemotron-generated code proposals through Nebius, reviewable diffs, explicit Trust authorizations, Docker regression comparisons on source snapshots, ProofGraph evidence, a retained multi-round failure-to-success history, and a second-file verification via an operator-supplied test.

**Not demonstrated as universal:** Fully autonomous multi-repository planning, unrestricted multi-file repairs, binary-format repairs, third-party-verified provenance, or correctness of every proposed code change.

**Recommended judge takeaway:** This is an evidence-driven **coding-agent prototype** that ties AI code generation to reviewable tests and retains failures rather than hiding them. The test scope is explicit and the operational safety boundary remains supervised.

**Thank you for evaluating the engineering system and its reproducible evidence.**
