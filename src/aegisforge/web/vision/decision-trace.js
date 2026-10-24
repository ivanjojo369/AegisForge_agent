/**
 * Quipu Vision — evidence-driven decision trace renderer (0.5 candidate).
 *
 * Distribution:
 *   Quipu-Vision/integrations/aegisforge/web/vision/decision-trace.js
 * Runtime after integration:
 *   AegisForge_agent/src/aegisforge/web/vision/decision-trace.js
 *
 * Reads the REAL `events` emitted by quipu_vision.workflow and exposed as
 * `output.results[index].events` by the AegisForge plugin. It never constructs
 * a fictional agent trace from a final action, invents tool calls, estimates
 * per-step timing/confidence, modifies decisions, or makes network requests.
 *
 * The current core's phases: observe -> plan -> act -> verify -> decision.
 * Some valid requests include only observe + decision; render the phases that
 * actually exist, in their original order. Event phases are data, not commands.
 *
 * Browser usage (once the host server serves this file):
 *   <script src="/demo/vision/decision-trace.js" defer></script>
 *   QuipuVisionDecisionTrace.renderTrace({
 *     container: document.getElementById('decision-trace'), artifact
 *   });
 *
 * Node.js tests: require('.../decision-trace.js').
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module && module.exports) module.exports = api;
  else if (root) root.QuipuVisionDecisionTrace = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const VERSION = '0.5.0-preview';
  const MAX_EVENTS = 64;
  const MAX_WARNINGS = 24;
  const MAX_TEXT = 240;
  const MAX_CASES = 8; // Existing plugin batch limit; not an assumed agent count.
  const KNOWN_PHASES = Object.freeze({
    observe: 'Observation',
    plan: 'Planning',
    act: 'Action',
    verify: 'Verification',
    decision: 'Decision',
  });

  function isRecord(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function cleaned(value) {
    if (typeof value !== 'string') return '';
    // No HTML interpolation: all output will be set through textContent.
    return value.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, MAX_TEXT);
  }

  function readable(value) {
    return value.replaceAll('_', ' ').replace(/\s+/g, ' ').trim();
  }

  function selectCase(artifact, caseIndex) {
    if (!isRecord(artifact)) throw new TypeError('Expected a Quipu Vision A2A artifact, output or result');
    if (!Number.isSafeInteger(caseIndex) || caseIndex < 0 || caseIndex >= MAX_CASES) {
      throw new RangeError(`caseIndex must be between 0 and ${MAX_CASES - 1}`);
    }
    const output = isRecord(artifact.output) ? artifact.output : artifact;
    const fromBatch = Array.isArray(output.results);
    const item = fromBatch ? output.results[caseIndex] : (caseIndex === 0 ? output : null);
    if (!isRecord(item)) throw new TypeError('Requested Quipu Vision result case does not exist');
    return { output, item, fromBatch };
  }

  /**
   * Extract reported events in their emitted order; do not synthesize steps.
   * `available` means a valid workflow event was received, not that AWS or a
   * physical scene condition has been independently verified.
   */
  function extractTrace(artifact, { caseIndex = 0 } = {}) {
    const { output, item, fromBatch } = selectCase(artifact, caseIndex);
    const evidence = isRecord(item.evidence) ? item.evidence
      : (isRecord(output.evidence) ? output.evidence : null);
    const failed = artifact.ok === false || output.ok === false ||
      output.status === 'error' || output.status === 'failed';

    // Error outcomes never masquerade as completed observation/verification.
    const inputEvents = failed ? [] : (Array.isArray(item.events) ? item.events
      : (evidence && Array.isArray(evidence.events) ? evidence.events : []));
    const events = [];
    let invalidEvents = 0;
    for (const raw of inputEvents.slice(0, MAX_EVENTS)) {
      if (!isRecord(raw)) { invalidEvents++; continue; }
      const phase = cleaned(raw.phase);
      const result = cleaned(raw.result);
      if (!phase || !result) { invalidEvents++; continue; }
      events.push(Object.freeze({ phase, result, label: KNOWN_PHASES[phase] || readable(phase) }));
    }

    const rawWarnings = [
      ...(Array.isArray(artifact.warnings) ? artifact.warnings : []),
      ...(output !== artifact && Array.isArray(output.warnings) ? output.warnings : []),
      ...(evidence && Array.isArray(evidence.warnings) ? evidence.warnings : []),
    ];
    const warnings = [];
    const seen = new Set();
    for (const entry of rawWarnings) {
      const warning = cleaned(entry);
      if (!warning || seen.has(warning)) continue;
      seen.add(warning);
      if (warnings.length < MAX_WARNINGS) warnings.push(warning);
    }

    const action = cleaned(item.next_action) || cleaned(output.next_action) ||
      (evidence ? cleaned(evidence.next_action) : '');
    const verifiedPixelPersistence = !failed && item.verified === true &&
      item.followup_processed === true;
    const elapsed = output.processing_ms;
    const processingMs = !failed && typeof elapsed === 'number' &&
      Number.isFinite(elapsed) && elapsed >= 0 ? elapsed : null;

    return Object.freeze({
      version: VERSION,
      caseIndex: fromBatch ? caseIndex : 0,
      failed,
      available: events.length > 0,
      events: Object.freeze(events),
      warnings: Object.freeze(warnings),
      nextAction: failed ? null : (action || null),
      followupProcessed: !failed && item.followup_processed === true,
      verifiedPixelPersistence,
      processingMs, // Whole request only; never attributed to individual phases.
      invalidEvents,
      omittedEvents: Math.max(0, inputEvents.length - MAX_EVENTS),
      omittedWarnings: Math.max(0, seen.size - MAX_WARNINGS),
      verifiedRealWorldSafety: false,
      source: 'reported_quipu_vision_workflow_events',
    });
  }

  function addText(doc, parent, tag, className, content) {
    const el = doc.createElement(tag);
    if (className) el.className = className;
    el.textContent = content;
    parent.appendChild(el);
    return el;
  }

  /**
   * Safe, dependency-free renderer. The caller supplies a DOM element and a
   * real artifact. Returns an extracted trace for optional additional UI.
   * Previous results are removed to avoid presenting a stale trace.
   */
  function renderTrace({ container, artifact, caseIndex = 0, showWarnings = true } = {}) {
    if (!container || typeof container.replaceChildren !== 'function' ||
        typeof container.appendChild !== 'function') {
      throw new TypeError('container must be a DOM element');
    }
    if (typeof showWarnings !== 'boolean') throw new TypeError('showWarnings must be boolean');
    const doc = container.ownerDocument || (typeof document !== 'undefined' ? document : null);
    if (!doc || typeof doc.createElement !== 'function') {
      throw new TypeError('container requires an ownerDocument with createElement');
    }
    const trace = extractTrace(artifact, { caseIndex });
    container.replaceChildren();
    container.setAttribute('data-quipu-decision-trace', VERSION);

    if (trace.failed) {
      addText(doc, container, 'p', 'qv-trace-status',
        'The inspection was unsuccessful. No completed decision trace is claimed.');
    } else if (!trace.available) {
      addText(doc, container, 'p', 'qv-trace-status',
        'No workflow events were included in this result.');
    } else {
      const list = doc.createElement('ol');
      list.className = 'qv-trace-list';
      list.setAttribute('aria-label', 'Recorded visual workflow events');
      trace.events.forEach((step, index) => {
        const row = doc.createElement('li');
        row.className = 'qv-trace-step';
        row.setAttribute('data-phase', step.phase);
        addText(doc, row, 'strong', 'qv-trace-phase', `${index + 1}. ${step.label}`);
        addText(doc, row, 'span', 'qv-trace-result', readable(step.result));
        list.appendChild(row);
      });
      container.appendChild(list);
    }

    if (!trace.failed && trace.nextAction) {
      const action = doc.createElement('p');
      action.className = 'qv-trace-next-action';
      addText(doc, action, 'strong', '', 'Recorded next action: ');
      addText(doc, action, 'span', '', readable(trace.nextAction));
      container.appendChild(action);
    }
    if (showWarnings && trace.warnings.length) {
      const heading = addText(doc, container, 'h4', 'qv-trace-warnings-title', 'Recorded limitations and warnings');
      heading.id = 'qv-trace-warnings';
      const list = doc.createElement('ul');
      list.className = 'qv-trace-warnings';
      for (const warning of trace.warnings) addText(doc, list, 'li', '', warning);
      container.appendChild(list);
    }
    addText(doc, container, 'p', 'qv-trace-disclaimer',
      'This shows recorded workflow events, not hidden model reasoning, object identity, or physical-safety certification.');
    return trace;
  }

  /** Plain-text export for review, without source images or private paths. */
  function traceToText(artifact, options = {}) {
    const trace = extractTrace(artifact, options);
    const lines = [`Quipu Vision trace — case ${trace.caseIndex + 1}`];
    if (trace.failed) lines.push('Inspection unsuccessful: no completed trace.');
    else if (!trace.available) lines.push('No recorded workflow events.');
    trace.events.forEach((event, index) => {
      lines.push(`${index + 1}. ${event.label}: ${readable(event.result)}`);
    });
    if (trace.nextAction) lines.push(`Next action: ${readable(trace.nextAction)}`);
    if (trace.warnings.length) {
      lines.push('Warnings:');
      trace.warnings.forEach(warning => lines.push(`- ${warning}`));
    }
    lines.push('No object identity or safety certification is implied.');
    return lines.join('\n');
  }

  return Object.freeze({
    version: VERSION,
    extractTrace,
    renderTrace,
    traceToText,
  });
});
