# Quipu Vision — Release procedure and final submission checklist

Current time reference: October 24, 2026. **Remote publication, clean-clone rebuild, AWS invocation and Devpost submission remain unverified.**

## 1. Safely freeze the final local source

At `AegisForge_agent/` run the `release_preflight.ps1` in `scripts/`; it is read-only. Confirm OpenCV 5, the web tests, Docker 8002 endpoints and clean `git diff --check` output. Record the actual source revisions. The initial remote SHAs observed before the latest integration were:

- `AegisForge_agent`: `f113757644721133fcd5a6dc36b4d1314846fbba`
- `Quipu-Vision`: `d6dd0d59f31c8c3f26ac75e7ca1711c642903106`

Do **not** present these old revisions as the source of the latest UI until a new push is verified.

## 2. Publish deliberately and only after inspection

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
git status --short
git diff --stat
git diff --check
# Inspect if Quipu-Vision contains a separate .git and how parent tracks it:
Test-Path .\Quipu-Vision\.git
git ls-files --stage -- Quipu-Vision
git -C .\Quipu-Vision rev-parse --show-toplevel
```

**Nested repository caveat:** If `Quipu-Vision` has its own `.git`, determine whether the parent uses a Git submodule/g​​itlink (`160000`) or a tracked folder snapshot before staging. The correct approach differs. Do not blindly run `git add .`, and do not accidentally add an embedded repository as a submodule. Review paths changed and publish to **both** intended repositories as needed. Commit/push intentionally from each correct repository root, using a clear message and confirmed branch. Never stage `.venv`, `.env`, secrets, runtime outputs, `integration-backups`, AWS credential files, or private media.

The desired content includes updated AegisForge `src/aegisforge/a2a_server.py`, `src/aegisforge/web/vision/{index.html,app.js,styles.css,image-quality.js,evidence-overlay.js,decision-trace.js}`, `src/aegisforge/web/evaluation-lab.html`, and the nested `Quipu-Vision` distributable modules/tests/experiments. Confirm the updated plugin/AWS integration copies are synchronized if they are part of the intended submission.

After publishing, record both final SHAs and verify links on GitHub **before** claiming the judges can clone the version shown in the video.

## 3. Rebuild from a fresh GitHub clone (AFTER push)

```powershell
$CheckDir = Join-Path $env:TEMP ('quipu-release-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $CheckDir | Out-Null
git clone https://github.com/ivanjojo369/AegisForge_agent.git "$CheckDir\AegisForge_agent"
git clone https://github.com/ivanjojo369/Quipu-Vision.git "$CheckDir\Quipu-Vision-standalone"
git -C "$CheckDir\AegisForge_agent" rev-parse HEAD
git -C "$CheckDir\Quipu-Vision-standalone" rev-parse HEAD
Set-Location "$CheckDir\AegisForge_agent"
docker build --target vision -t aegisforge-vision:github-release .
docker run -d --rm --name quipu-vision-clean-check -p 127.0.0.1:8003:8001 aegisforge-vision:github-release
Invoke-RestMethod http://127.0.0.1:8003/health
Invoke-RestMethod http://127.0.0.1:8003/vision/capabilities
```

Open `http://127.0.0.1:8003/demo/vision` and repeat a real `inspect` and `verify` using image files on your **local workstation**. `examples/` is excluded from the Docker image by `.dockerignore`, so it does not contain its own demonstration images. A successful build or GET-only health check does not validate image processing. If the standalone and embedded copies differ, resolve differences rather than silently overwriting either one. This procedure uses port 8003 to avoid interference with your existing 8001/8002 servers.

## 4. AWS mandatory proof

Follow `AWS_DEPLOYMENT.md` with an authorized AWS account. A real signed A2A image request must finish successfully and provide a sanitized result plus CloudWatch evidence. No AWS success is assumed.

## 5. Finish Devpost/video/docs

- [ ] Correct source links and final SHA(s) in the technical report.
- [ ] Architecture diagram separates confirmed local from actual AWS execution.
- [ ] Video is public/unlisted, judge-accessible, <= 5 minutes, with real application and team/developer identification.
- [ ] Evidence bundle includes 33/33 Python and 63/63 web tests (last verified), 24 synthetic case metrics, 7-case persistence, failure modes and version statements.
- [ ] A significant OpenCV 5 workload was really executed on AWS and logged.
- [ ] Judges can use authorized endpoint OR a formally arranged live screen-share.
- [ ] Verify eligibility and permissions under the official rules.
- [ ] Submit all Devpost required fields/links and capture final submission confirmation BEFORE the deadline.

Official deadline header: **October 26, 2026, 11:45 p.m. PDT** (earlier than the page overview's 11:59 p.m. description). Plan for the earlier cutoff.

Official site: https://opencv26.devpost.com/ ; rules: https://opencv26.devpost.com/rules
