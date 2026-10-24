#!/usr/bin/env python3
"""Integrate Quipu Vision 0.5-preview web enhancements into AegisForge.

Default: read-only preview. --apply: transactional backups then updates.
Touches only AegisForge's existing four web/server files plus four allowed web assets.
No network access, no changes to the OpenCV engine or AegisForge's other routes.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import shutil
import sys

MARK = "Quipu Vision web 0.5 integration"


def replace_one(data: str, anchor: str, replacement: str, label: str) -> str:
    count = data.count(anchor)
    if count != 1:
        raise ValueError(f"{label}: expected exactly one anchor, found {count}; refusing to modify")
    return data.replace(anchor, replacement, 1)


def patch_html(data: str) -> str:
    if 'id="qv-quality-panel"' in data:
        if 'id="qv-trace-panel"' not in data or '/demo/vision/image-quality.js' not in data:
            raise ValueError("HTML seems only partially integrated; inspect manually")
        return data
    marker = '  <script src="/demo/vision/app.js" defer></script>'
    data = replace_one(data, marker, '''  <!-- Quipu Vision web 0.5 integration: scripts execute in document order. -->
  <script src="/demo/vision/image-quality.js" defer></script>
  <script src="/demo/vision/evidence-overlay.js" defer></script>
  <script src="/demo/vision/decision-trace.js" defer></script>
'''+marker, "HTML JS dependencies")
    marker = '            <p id="form-feedback" class="form-feedback" role="status" aria-live="polite">Select two images to begin.</p>'
    data = replace_one(data, marker, marker + '''
            <section id="qv-quality-panel" class="qv-quality-panel" aria-labelledby="qv-quality-title" hidden>
              <h3 id="qv-quality-title">Image quality preflight</h3>
              <p id="qv-quality-summary" class="qv-note"></p>
              <ul id="qv-quality-warnings" class="qv-quality-warnings"></ul>
              <p class="qv-note">Browser advisories only: OpenCV results and decisions are unchanged.</p>
            </section>''', "HTML quality panel")
    marker = '            <section class="result-subsection" aria-labelledby="evidence-title">'
    data = replace_one(data, marker, '''            <section class="result-subsection" id="qv-overlay-panel" aria-labelledby="qv-overlay-title" hidden>
              <h3 id="qv-overlay-title">Visual evidence overlays</h3>
              <p class="qv-note" id="qv-overlay-status">Regions originate from the server's OpenCV result; no object recognition is implied.</p>
              <div class="qv-overlay-grid">
                <figure id="qv-observed-figure" class="qv-overlay-figure" hidden>
                  <canvas id="qv-observed-canvas" aria-label="Observed image with pixel-change regions"></canvas>
                  <figcaption>Initial observation · OpenCV detected regions</figcaption>
                </figure>
                <figure id="qv-followup-figure" class="qv-overlay-figure" hidden>
                  <canvas id="qv-followup-canvas" aria-label="Follow-up image with pixel-change regions"></canvas>
                  <figcaption>Follow-up · only if the server processed that image</figcaption>
                </figure>
              </div>
            </section>

'''+marker, "HTML overlays")
    marker = '              <details class="raw-result"><summary>View structured JSON response</summary><pre id="raw-json" tabindex="0"></pre></details>'
    data = replace_one(data, marker, '''              <section id="qv-trace-panel" class="qv-trace-panel" aria-labelledby="qv-trace-title" hidden>
                <h3 id="qv-trace-title">Auditable decision trace</h3>
                <div id="qv-decision-trace"></div>
                <button id="qv-download-trace" class="button button-secondary qv-trace-download" type="button" disabled>Download trace (.txt)</button>
              </section>
'''+marker, "HTML decision trace")
    marker = '<span>Local-first visual inspection · OpenCV 5</span>'
    data = replace_one(data, marker, '<span>Local-first visual inspection · OpenCV 5 · <a href="/demo/vision/evaluation-lab.html">Evaluation Lab</a></span>', "HTML evaluation lab link")
    return data


def patch_js(data: str) -> str:
    if 'function renderWebEvidence(artifact)' in data:
        if 'await reportImageQuality(files);' not in data:
            raise ValueError("JS appears partially integrated; refusing to continue")
        return data
    # clearResult() clears the region table immediately before the event list.
    # renderEvents() also calls events.replaceChildren(), so that line alone
    # is ambiguous in the actual AegisForge app.js. Match both adjacent lines.
    marker = ('    elements.regionsBody.replaceChildren();\n'
              '    elements.events.replaceChildren();')
    data = replace_one(data, marker, marker + '\n    clearWebEnhancements();', 'app.js clearResult')
    marker = '      await validateDimensions(files);'
    data = replace_one(data, marker, marker + '\n      await reportImageQuality(files);', 'app.js preflight')
    marker = '      renderResult(artifact);'
    data = replace_one(data, marker, marker + '\n      void renderWebEvidence(artifact);', 'app.js after task')
    marker = '  elements.form.addEventListener("submit", async (event) => {'
    new_functions = r'''  // Quipu Vision web 0.5 integration. These advisory UI helpers do not
  // intercept, alter, suppress or reclassify A2A results.
  let qvLastArtifact = null;

  function clearWebEnhancements() {
    qvLastArtifact = null;
    const quality = $("qv-quality-panel");
    const overlays = $("qv-overlay-panel");
    const trace = $("qv-trace-panel");
    if (quality) quality.hidden = true;
    if (overlays) overlays.hidden = true;
    if (trace) trace.hidden = true;
    const observed = $("qv-observed-figure");
    const followup = $("qv-followup-figure");
    if (observed) observed.hidden = true;
    if (followup) followup.hidden = true;
    const traceContent = $("qv-decision-trace");
    if (traceContent) traceContent.replaceChildren();
    const download = $("qv-download-trace");
    if (download) download.disabled = true;
  }

  async function reportImageQuality(files) {
    const panel = $("qv-quality-panel");
    const analyzer = window.QuipuVisionImageQuality;
    if (!panel || !analyzer || typeof analyzer.analyzeFiles !== "function") return;
    const summary = $("qv-quality-summary");
    const list = $("qv-quality-warnings");
    list.replaceChildren();
    panel.hidden = false;
    try {
      // Only the user's local File objects; no image data is sent by this module.
      const byRole = Object.fromEntries(files.map(({ role, file }) => [role, file]));
      const report = await analyzer.analyzeFiles(byRole);
      const issues = Array.isArray(report?.comparison?.issues) ? report.comparison.issues : [];
      summary.textContent = issues.length
        ? `${issues.length} capture-quality advisory notice(s). The inspection will still run.`
        : "No quality warnings from this optional browser preflight.";
      for (const issue of issues.slice(0, 8)) {
        const li = document.createElement("li");
        li.textContent = String(issue?.message || issue?.code || "Capture quality warning").slice(0, 350);
        list.appendChild(li);
      }
      if (issues.length > 8) {
        const li = document.createElement("li");
        li.textContent = `${issues.length - 8} additional notice(s) omitted.`;
        list.appendChild(li);
      }
    } catch {
      summary.textContent = "Image-quality preflight unavailable. Original OpenCV inspection will continue.";
    }
  }

  async function renderWebEvidence(artifact) {
    qvLastArtifact = artifact;
    const tracer = window.QuipuVisionDecisionTrace;
    const tracePanel = $("qv-trace-panel");
    if (tracer && tracePanel && typeof tracer.renderTrace === "function") {
      try {
        tracer.renderTrace({ container: $("qv-decision-trace"), artifact, showWarnings: false });
        tracePanel.hidden = false;
        $("qv-download-trace").disabled = false;
      } catch {
        tracePanel.hidden = true;
      }
    }
    const tool = window.QuipuVisionEvidenceOverlay;
    const panel = $("qv-overlay-panel");
    if (!tool || !panel || typeof tool.extractEvidence !== "function") return;
    try {
      const evidence = tool.extractEvidence(artifact);
      const renderOne = async (role, stage, part) => {
        if (!part) return false;
        const image = $(`${role}-image`);
        if (!image || !image.getAttribute("src")) return false;
        // A2A may return before the browser finishes loading the image preview.
        if (!image.complete || !image.naturalWidth) await image.decode();
        if (qvLastArtifact !== artifact) return false;
        const canvas = $(`qv-${role}-canvas`);
        tool.drawOverlay({ canvas, image, ...part, stage, maxWidth: 920, maxHeight: 640 });
        $(`qv-${role}-figure`).hidden = false;
        return true;
      };
      const initial = await renderOne("observed", "observed", evidence.observed);
      const followup = await renderOne("followup", "followup", evidence.followup);
      if (qvLastArtifact !== artifact) return;
      panel.hidden = !(initial || followup);
      if (initial || followup) {
        $("qv-overlay-status").textContent =
          "Colored boxes indicate pixel-difference regions measured by OpenCV. They do not identify objects or certify safety.";
      }
    } catch {
      // An optional visualization failure must never change task success.
      panel.hidden = true;
    }
  }

  const traceDownloadButton = $("qv-download-trace");
  if (traceDownloadButton) traceDownloadButton.addEventListener("click", () => {
    const tracer = window.QuipuVisionDecisionTrace;
    if (!qvLastArtifact || !tracer || typeof tracer.traceToText !== "function") return;
    try {
      const content = tracer.traceToText(qvLastArtifact);
      const blob = new Blob([content + "\n"], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "quipu-vision-decision-trace.txt";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      // No fallback that could expose private image payloads or paths.
    }
  });

'''
    data = replace_one(data, marker, new_functions + marker, 'app.js helper insertion')
    return data


STYLES = r'''
/* Quipu Vision web 0.5 integration: optional capture advisory, overlays and trace. */
.qv-quality-panel, .qv-trace-panel { margin-top: 17px; padding: 16px; border: 1px solid var(--line); border-radius: 11px; background: rgba(13, 34, 49, .65); }
.qv-quality-panel h3, .qv-trace-panel h3 { margin: 0 0 9px; font-size: .94rem; }
.qv-note { color: var(--text-secondary); font-size: .78rem; line-height: 1.55; margin: 7px 0; }
.qv-quality-warnings { margin: 8px 0; padding-left: 22px; color: var(--warning); font-size: .79rem; line-height: 1.55; overflow-wrap: anywhere; }
.qv-quality-warnings li { margin: 6px 0; }
.qv-overlay-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 270px), 1fr)); gap: 13px; }
.qv-overlay-figure { margin: 0; padding: 10px; border-radius: 11px; border: 1px solid var(--line-soft); background: #0b1727; min-width: 0; }
.qv-overlay-figure canvas { display: block; width: 100%; height: auto; max-width: 100%; border-radius: 8px; }
.qv-overlay-figure figcaption { color: var(--text-secondary); font-size: .73rem; line-height: 1.55; margin-top: 9px; }
.qv-trace-list { margin: 11px 0; padding-left: 22px; display: grid; gap: 10px; }
.qv-trace-step { color: var(--text-secondary); line-height: 1.4; font-size: .78rem; }
.qv-trace-phase { display: block; color: var(--accent-strong); margin-bottom: 2px; }
.qv-trace-result { display: block; overflow-wrap: anywhere; }
.qv-trace-next-action, .qv-trace-disclaimer { color: var(--text-secondary); font-size: .75rem; line-height: 1.55; }
.qv-trace-download { margin-top: 12px; }
.site-footer a { color: var(--accent-strong); text-decoration: underline; text-underline-offset: 3px; }
'''


def patch_css(data: str) -> str:
    if '/* Quipu Vision web 0.5 integration:' in data:
        return data
    return data.rstrip() + '\n\n' + STYLES + '\n'


def patch_server(data: str) -> str:
    if '"image-quality.js": "text/javascript; charset=utf-8",' in data:
        if 'Route("/demo/vision/evaluation-lab.html"' not in data:
            raise ValueError("Server partially integrated; refusing to modify")
        return data
    data = data.replace('Only these three named assets are', 'Only these named allowlisted assets are')
    marker = '    "styles.css": "text/css; charset=utf-8",'
    data = replace_one(data, marker, marker + '''
    # Quipu Vision web 0.5 integration: hardcoded assets only (never arbitrary paths).
    "image-quality.js": "text/javascript; charset=utf-8",
    "evidence-overlay.js": "text/javascript; charset=utf-8",
    "decision-trace.js": "text/javascript; charset=utf-8",
    "evaluation-lab.html": "text/html; charset=utf-8",''', 'server asset allowlist')
    marker = '''    candidates = (
        Path(__file__).resolve().parent / "web" / "vision" / filename,
        Path.cwd() / "src" / "aegisforge" / "web" / "vision" / filename,
    )'''
    new = '''    # Evaluation Lab is a separate static page at web/evaluation-lab.html.
    # All names are checked against the fixed allowlist above.
    relative = (Path("web") / "evaluation-lab.html" if filename == "evaluation-lab.html"
                else Path("web") / "vision" / filename)
    candidates = (
        Path(__file__).resolve().parent / relative,
        Path.cwd() / "src" / "aegisforge" / relative,
    )'''
    data = replace_one(data, marker, new, 'server asset directory')
    marker = '            Route("/demo/vision/styles.css", lambda _request: _vision_web_response("styles.css"), methods=["GET"]),'
    data = replace_one(data, marker, marker + '''
            Route("/demo/vision/image-quality.js", lambda _request: _vision_web_response("image-quality.js"), methods=["GET"]),
            Route("/demo/vision/evidence-overlay.js", lambda _request: _vision_web_response("evidence-overlay.js"), methods=["GET"]),
            Route("/demo/vision/decision-trace.js", lambda _request: _vision_web_response("decision-trace.js"), methods=["GET"]),
            Route("/demo/vision/evaluation-lab.html", lambda _request: _vision_web_response("evaluation-lab.html"), methods=["GET"]),''', 'server routes')
    # Keep the separate Evaluation Lab page's restrictive HTML meta CSP.
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='AegisForge_agent root; autodetected from script location if omitted')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true', help='Back up and modify the four host files, then copy approved assets')
    group.add_argument('--check', action='store_true', help='Read-only: verify that integration has been applied')
    args = parser.parse_args()
    script = Path(__file__).resolve()
    root = (args.root or script.parents[2]).expanduser().resolve()
    qv = root / 'Quipu-Vision'
    sources = qv / 'integrations' / 'aegisforge' / 'web'
    destination = root / 'src' / 'aegisforge'
    four = {
        destination / 'web' / 'vision' / 'index.html': patch_html,
        destination / 'web' / 'vision' / 'app.js': patch_js,
        destination / 'web' / 'vision' / 'styles.css': patch_css,
        destination / 'a2a_server.py': patch_server,
    }
    copies = {
        sources / 'vision' / name: destination / 'web' / 'vision' / name
        for name in ['image-quality.js', 'evidence-overlay.js', 'decision-trace.js']
    }
    copies[sources / 'evaluation-lab.html'] = destination / 'web' / 'evaluation-lab.html'
    try:
        plan = {}
        for dest, transform in four.items():
            raw = dest.read_text(encoding='utf-8')
            changed = transform(raw)
            plan[dest] = (raw, changed)
        for source in copies:
            if not source.is_file():
                raise FileNotFoundError(f'Expected asset missing: {source}')
        updates = [(p, data) for p, (old, data) in plan.items() if old != data]
        for s, d in copies.items():
            if not d.is_file() or d.read_bytes() != s.read_bytes():
                updates.append((d, s.read_bytes()))
        if args.check:
            if updates:
                print(f'NOT YET INTEGRATED: {len(updates)} file(s) differ')
                return 1
            print('INTEGRATED: 4 host files and 4 static assets verified.')
            return 0
        print(f'AegisForge root: {root}')
        print(f'Changes planned: {len(updates)} file(s)')
        for path, _ in updates:
            print('  UPDATE' if path.exists() else '  CREATE', path.relative_to(root))
        if not args.apply:
            print('Dry-run only: no files modified. Use --apply to perform this integration.')
            return 0
        if not updates:
            print('Nothing to apply; integration is already current.')
            return 0
        backup_folder = qv / 'outputs' / 'integration-backups' / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        if backup_folder.exists():
            raise FileExistsError(f'Backup directory already exists: {backup_folder}')
        for path, _ in updates:
            if path.exists():
                target = backup_folder / path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        written = []
        try:
            for path, content in updates:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + '.qv-new')
                raw = content.encode('utf-8') if isinstance(content, str) else content
                try:
                    tmp.write_bytes(raw)
                    tmp.replace(path)
                finally:
                    if tmp.exists():
                        tmp.unlink()
                written.append(path)
        except Exception:
            for path in reversed(written):
                old = backup_folder / path.relative_to(root)
                if old.exists():
                    shutil.copy2(old, path)
                else:
                    path.unlink(missing_ok=True)
            raise
        print(f'Applied successfully. Backup originals at: {backup_folder}')
        print('Validate: python Quipu-Vision/tools/integrate_web.py --check')
        print('Web test: node --test Quipu-Vision/tests/web/*.test.mjs')
        print('Restart the AegisForge A2A server to publish the new static routes.')
        return 0
    except (OSError, ValueError, UnicodeError) as exc:
        print(f'Integration refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
