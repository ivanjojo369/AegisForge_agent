/**
 * Tests for the standalone Quipu Vision decision-trace renderer.
 * Run from Quipu-Vision/: node --test tests/web/decision-trace.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const trace = require('../../integrations/aegisforge/web/vision/decision-trace.js');

class FakeElement {
  constructor(tag, doc) {
    this.tagName = tag;
    this.ownerDocument = doc;
    this.children = [];
    this.className = '';
    this.attrs = {};
    this.textContent = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
}
const doc = { createElement(tag) { return new FakeElement(tag, doc); } };
const container = () => new FakeElement('div', doc);
const events = [
  { phase: 'observe', result: 'change_detected' },
  { phase: 'plan', result: 'request_second_observation' },
  { phase: 'act', result: 'process_supplied_followup_image' },
  { phase: 'verify', result: 'persistent_pixel_change' },
  { phase: 'decision', result: 'request_human_review' },
];
function task({ followup = true, fail = false, eventData = null } = {}) {
  return {
    ok: !fail,
    warnings: ['Changes are pixel differences, not objects.'],
    output: {
      status: fail ? 'error' : 'completed',
      processing_ms: 13.5,
      results: [{
        status: 'persistent_change_observed',
        followup_processed: followup,
        verified: followup,
        next_action: followup ? 'request_human_review' : 'request_second_observation',
        events: eventData ?? (followup ? events : events.slice(0, 2)),
        evidence: { warnings: ['Human review required.'] },
      }],
    },
  };
}
function flattenText(node) { return [node.textContent, ...node.children.map(flattenText)].join(' '); }

test('exports frozen, side-effect-free API', () => {
  assert.equal(Object.isFrozen(trace), true);
  assert.equal(typeof trace.extractTrace, 'function');
  assert.equal(typeof trace.renderTrace, 'function');
  assert.equal(typeof trace.traceToText, 'function');
});
test('extracts real phases in the original order', () => {
  const result = trace.extractTrace(task());
  assert.deepEqual(result.events.map(e => e.phase), ['observe', 'plan', 'act', 'verify', 'decision']);
  assert.equal(result.nextAction, 'request_human_review');
  assert.equal(result.followupProcessed, true);
  assert.equal(result.verifiedPixelPersistence, true);
  assert.equal(result.verifiedRealWorldSafety, false);
  assert.equal(result.processingMs, 13.5);
});
test('inspect-only result never invents act or verification', () => {
  const result = trace.extractTrace(task({ followup: false }));
  assert.deepEqual(result.events.map(e => e.phase), ['observe', 'plan']);
  assert.equal(result.followupProcessed, false);
  assert.equal(result.verifiedPixelPersistence, false);
});
test('without events, no phases are fabricated from next_action', () => {
  const result = trace.extractTrace(task({ eventData: [] }));
  assert.equal(result.available, false);
  assert.equal(result.events.length, 0);
  assert.equal(result.nextAction, 'request_human_review');
});
test('uses original engine events when plugin event list is absent', () => {
  const obj = task();
  delete obj.output.results[0].events;
  obj.output.results[0].evidence.events = [{ phase: 'observe', result: 'no_significant_change' }];
  assert.equal(trace.extractTrace(obj).events[0].result, 'no_significant_change');
});
test('supports direct case object and output object', () => {
  const obj = task();
  assert.equal(trace.extractTrace(obj.output).events.length, 5);
  assert.equal(trace.extractTrace(obj.output.results[0]).events.length, 5);
});
test('selects independent batch case without mixing events', () => {
  const obj = task();
  obj.output.results.push({ events: [{ phase: 'observe', result: 'no_significant_change' }], next_action: 'continue_monitoring' });
  const result = trace.extractTrace(obj, { caseIndex: 1 });
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].result, 'no_significant_change');
  assert.equal(result.nextAction, 'continue_monitoring');
});
test('rejects missing cases and invalid caseIndex', () => {
  assert.throws(() => trace.extractTrace(null), /Expected/);
  assert.throws(() => trace.extractTrace({ output: { results: [] } }), /does not exist/);
  assert.throws(() => trace.extractTrace(task(), { caseIndex: -1 }), /caseIndex/);
  assert.throws(() => trace.extractTrace(task(), { caseIndex: 10 }), /caseIndex/);
});
test('fails closed for explicitly failed task', () => {
  const result = trace.extractTrace(task({ fail: true }));
  assert.equal(result.failed, true);
  assert.equal(result.available, false);
  assert.equal(result.nextAction, null);
  assert.equal(result.verifiedPixelPersistence, false);
  assert.equal(result.events.length, 0);
});
test('skips malformed event entries rather than inventing labels', () => {
  const result = trace.extractTrace(task({ eventData: [null, {}, { phase: 'plan' }, { phase: 'custom_phase', result: 'new_tool_call' }] }));
  assert.equal(result.invalidEvents, 3);
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].label, 'custom phase');
});
test('limits events, warnings and text length', () => {
  const obj = task({ eventData: Array.from({ length: 70 }, () => ({ phase: 'observe', result: 'X'.repeat(500) })) });
  obj.warnings = Array.from({ length: 30 }, (_, i) => `warning ${i}`);
  const result = trace.extractTrace(obj);
  assert.equal(result.events.length, 64);
  assert.equal(result.omittedEvents, 6);
  assert.equal(result.events[0].result.length, 240);
  assert.equal(result.warnings.length, 24);
  assert.equal(result.omittedWarnings, 7); // 30 plus distinct evidence warning
});
test('deduplicates identical warning strings', () => {
  const obj = task();
  obj.warnings = ['review', 'review'];
  obj.output.results[0].evidence.warnings = ['review'];
  assert.deepEqual(trace.extractTrace(obj).warnings, ['review']);
});
test('no invented step latency or confidence', () => {
  const result = trace.extractTrace(task());
  assert.equal(result.events.some(event => 'latencyMs' in event || 'confidence' in event), false);
  assert.equal('confidence' in result, false);
});
test('renderTrace creates ordered timeline and next-action text', () => {
  const el = container();
  const result = trace.renderTrace({ container: el, artifact: task() });
  assert.equal(result.events.length, 5);
  assert.equal(el.children[0].tagName, 'ol');
  assert.equal(el.children[0].children.length, 5);
  assert.equal(el.children[0].children[0].attrs['data-phase'], 'observe');
  assert.match(flattenText(el), /Recorded next action/);
});
test('renderer removes stale events on subsequent run', () => {
  const el = container();
  trace.renderTrace({ container: el, artifact: task() });
  trace.renderTrace({ container: el, artifact: task({ eventData: [] }) });
  assert.equal(el.children.some(c => c.tagName === 'ol'), false);
  assert.match(flattenText(el), /No workflow events/);
});
test('renderer avoids inventing completed trace when task failed', () => {
  const el = container();
  trace.renderTrace({ container: el, artifact: task({ fail: true }) });
  assert.match(flattenText(el), /unsuccessful/);
  assert.doesNotMatch(flattenText(el), /Recorded next action/);
});
test('malicious input is displayed via textContent, never innerHTML', () => {
  const el = container();
  const obj = task({ eventData: [{ phase: '<img src=x onerror=alert(1)>', result: '<script>alert(1)</script>' }] });
  trace.renderTrace({ container: el, artifact: obj });
  assert.match(flattenText(el), /<script>/);
  assert.equal(el.children.some(c => c.tagName === 'script'), false);
});
test('renderTrace optional warnings and invalid containers', () => {
  const el = container();
  trace.renderTrace({ container: el, artifact: task(), showWarnings: false });
  assert.equal(el.children.some(c => c.className === 'qv-trace-warnings'), false);
  assert.throws(() => trace.renderTrace({ artifact: task() }), /container/);
  assert.throws(() => trace.renderTrace({ container: el, artifact: task(), showWarnings: 'no' }), /showWarnings/);
});
test('text export describes only reported events and limitations', () => {
  const out = trace.traceToText(task({ followup: false }));
  assert.match(out, /Observation: change detected/);
  assert.doesNotMatch(out, /Verification:/);
  assert.match(out, /Next action: request second observation/);
  assert.match(out, /No object identity or safety certification/);
});
