/**
 * Quipu Vision evidence overlay regression checks.
 * Run from Quipu-Vision/: node --test tests/web/evidence-overlay.test.mjs
 * Node 18+, no packages or browser required.
 * Tests canvas geometry, A2A evidence extraction and non-inference guarantees.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const overlay = require('../../integrations/aegisforge/web/vision/evidence-overlay.js');

function canvas() {
  const calls = { draw: [], stroke: [], fill: [], labels: [], attrs: {}, dash: [] };
  const ctx = {
    drawImage(...args) { calls.draw.push(args); },
    strokeRect(...args) { calls.stroke.push(args); },
    fillRect(...args) { calls.fill.push(args); },
    fillText(...args) { calls.labels.push(args); },
    setLineDash(v) { calls.dash.push([...v]); },
    strokeStyle: '',
    fillStyle: '',
    lineWidth: 1,
  };
  const el = {
    width: 100,
    height: 100,
    getContext(type) { assert.equal(type, '2d'); return ctx; },
    setAttribute(key, val) { calls.attrs[key] = val; },
  };
  return { el, ctx, calls };
}
const img = (width = 400, height = 200) => ({ naturalWidth: width, naturalHeight: height });
const region = (x, y, w, h) => ({ bbox_xywh: [x, y, w, h], area_px: w * h, area_fraction: 0.1 });
const box = (x, y, w, h) => ({ bbox_xywh: [x, y, w, h] });

function task({ followup = true } = {}) {
  const initial = { roi_xywh: [0, 0, 400, 200], regions: [region(10, 20, 30, 40)], regions_total: 1 };
  const later = { roi_xywh: [0, 0, 400, 200], regions: [region(20, 24, 34, 40)], regions_total: 1 };
  return {
    ok: true,
    output: { status: 'completed', results: [{
      regions: initial.regions, regions_total: 1,
      verified: followup,
      followup_processed: followup,
      next_action: followup ? 'request_human_review' : 'request_second_observation',
      evidence: { observation: initial, verification: followup ? { observation: later, persistent_region: true } : null },
    }] },
  };
}

test('exports stable function API', () => {
  assert.equal(typeof overlay.drawOverlay, 'function');
  assert.equal(typeof overlay.extractEvidence, 'function');
  assert.equal(typeof overlay.drawFromArtifact, 'function');
  assert.equal(Object.isFrozen(overlay), true);
});
test('reads observed and distinct follow-up boxes from real A2A result shape', () => {
  const e = overlay.extractEvidence(task());
  assert.deepEqual(e.observed.regions[0].bbox_xywh, [10, 20, 30, 40]);
  assert.deepEqual(e.followup.regions[0].bbox_xywh, [20, 24, 34, 40]);
  assert.equal(e.verifiedPixelPersistence, true);
  assert.equal(e.nextAction, 'request_human_review');
});
test('never invents follow-up when no follow-up was processed', () => {
  const e = overlay.extractEvidence(task({ followup: false }));
  assert.equal(e.followup, null);
  assert.equal(e.verifiedPixelPersistence, false);
});
test('supports single-case and output-only variants', () => {
  const t = task();
  assert.equal(overlay.extractEvidence(t.output.results[0]).observed.regions.length, 1);
  assert.equal(overlay.extractEvidence(t.output).observed.regions.length, 1);
});
test('omitted evidence returns raw top-level regions, no fake ROI', () => {
  const e = overlay.extractEvidence({ regions: [box(0, 0, 1, 1)], regions_total: 5 });
  assert.equal(e.observed.roi, null);
  assert.equal(e.observed.regionsTotal, 5);
  assert.equal(e.followup, null);
});
test('correctly draws and scales boxes from full-resolution coordinates', () => {
  const c = canvas();
  const output = overlay.drawOverlay({ canvas: c.el, image: img(400, 200), regions: [region(10, 20, 30, 40)], maxWidth: 200, maxHeight: 100, showLabels: false });
  assert.equal(c.el.width, 200);
  assert.equal(c.el.height, 100);
  assert.deepEqual(c.calls.draw[0].slice(1), [0, 0, 200, 100]);
  assert.deepEqual(c.calls.stroke[0], [5, 10, 15, 20]);
  assert.equal(output.drawnRegions, 1);
  assert.equal(output.inferencePerformed, false);
});
test('draws ROI in image coordinates and dashed without modifying image', () => {
  const c = canvas();
  const image = img();
  const result = overlay.drawOverlay({ canvas: c.el, image, roi: [20, 30, 100, 80], regions: [box(30, 35, 5, 5)], showLabels: false });
  assert.deepEqual(c.calls.stroke[0], [20, 30, 100, 80]);
  assert.deepEqual(c.calls.dash, [[6, 4], []]);
  assert.equal(result.roiDrawn, true);
  assert.equal(image.naturalWidth, 400);
});
test('drawing colors do not imply verified object identity or safety', () => {
  const c = canvas();
  overlay.drawOverlay({canvas: c.el, image: img(), stage: 'followup', regions: [box(0,0,50,30)]});
  assert.match(c.calls.attrs['aria-label'], /pixel differences, not object identity or safety/i);
  assert.equal(c.calls.labels[0][0], '#1');
});
test('zero changes means no invented rectangle', () => {
  const c = canvas();
  const result = overlay.drawOverlay({canvas:c.el, image:img(), regions:[]});
  assert.equal(c.calls.draw.length, 1);
  assert.equal(c.calls.stroke.length, 0);
  assert.equal(result.drawnRegions, 0);
});
test('rejects coordinates that would misrepresent evidence', () => {
  const c = canvas();
  const result = overlay.drawOverlay({canvas:c.el, image:img(), regions:[
    box(-1,0,10,10), box(399,100,3,3), box(10.3,10,20,20),
    box(0,0,0,10), box(0,0,20,20),
  ],showLabels:false});
  assert.equal(result.invalidRegions, 4);
  assert.equal(result.drawnRegions, 1);
  assert.equal(c.calls.stroke.length, 1);
});
test('limits to top twelve region boxes to bound drawing work', () => {
  const c = canvas();
  const result = overlay.drawOverlay({canvas:c.el, image:img(), regions:Array.from({length:16},(_,i)=>box(i,0,4,4)),showLabels:false});
  assert.equal(result.drawnRegions,12);
  assert.equal(result.omittedRegions,4);
});
test('large images are downsampled without exceeding render pixel limit', () => {
  const c = canvas();
  const result = overlay.drawOverlay({canvas:c.el, image:img(4000,3000), regions:[] ,maxWidth:4096,maxHeight:4096});
  assert.ok(result.canvasWidth*result.canvasHeight <= 1048576);
  assert.equal(result.sourceWidth,4000);
});
test('rejects impossible image sizes, stages and canvases', () => {
  const c = canvas();
  assert.throws(()=>overlay.drawOverlay({canvas:c.el,image:img(4000,3500)}),/12 MP/);
  assert.throws(()=>overlay.drawOverlay({canvas:c.el,image:img(),stage:'reference'}),/stage/);
  assert.throws(()=>overlay.drawOverlay({image:img()}),/canvas/);
  assert.throws(()=>overlay.drawOverlay({canvas:c.el,image:img(),regions:{}}),/array/);
});
test('rejects ROI outside original bounds and invalid rendering limits', () => {
  const c = canvas();
  assert.throws(()=>overlay.drawOverlay({canvas:c.el,image:img(),roi:[390,10,20,20]}),/roi/);
  assert.throws(()=>overlay.drawOverlay({canvas:c.el,image:img(),maxWidth:2}),/maxWidth/);
});
test('drawFromArtifact draws initial and follow-up independently', () => {
  const a = canvas(); const b = canvas();
  const r = overlay.drawFromArtifact({artifact:task(),observed:{canvas:a.el,image:img()},followup:{canvas:b.el,image:img()}});
  assert.equal(r.observed.drawnRegions,1);
  assert.equal(r.followup.drawnRegions,1);
  assert.deepEqual(a.calls.stroke[1],[10,20,30,40]);
  assert.deepEqual(b.calls.stroke[1],[20,24,34,40]);
});
test('drawFromArtifact clears stale follow-up canvas for an inspect-only task', () => {
  const a = canvas(); const b = canvas();
  const r = overlay.drawFromArtifact({artifact:task({followup:false}),observed:{canvas:a.el,image:img()},followup:{canvas:b.el,image:img()}});
  assert.equal(r.followup,null);
  assert.equal(b.el.width,0);
  assert.match(b.calls.attrs['aria-label'],/No processed follow-up/);
});
test('rejects nonexistent result case instead of fabricating evidence', () => {
  assert.throws(()=>overlay.extractEvidence({output:{results:[]}}),/Missing/);
  assert.throws(()=>overlay.extractEvidence(null),/Expected/);
});
