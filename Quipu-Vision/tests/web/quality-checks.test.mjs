/**
 * Quipu Vision — regression tests for the advisory image-quality module.
 *
 * Location: Quipu-Vision/tests/web/quality-checks.test.mjs
 * Run from Quipu-Vision/: node --test tests/web/quality-checks.test.mjs
 * Requires Node.js 18+; no npm dependencies, no AWS, no browser needed.
 *
 * These tests do NOT measure OpenCV detector accuracy or make safety claims.
 * They verify heuristic advisory behavior and compatibility only.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const quality = require('../../integrations/aegisforge/web/vision/image-quality.js');

function image(width, height, rgb, { clamped = true } = {}) {
  const data = clamped
    ? new Uint8ClampedArray(width * height * 4)
    : new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const rgba = typeof rgb === 'function' ? rgb(x, y) : rgb;
      const i = (y * width + x) * 4;
      data[i] = rgba[0];
      data[i + 1] = rgba[1];
      data[i + 2] = rgba[2];
      data[i + 3] = rgba[3] ?? 255;
    }
  }
  return { width, height, data };
}

const black = (width = 96, height = 96) => image(width, height, [0, 0, 0]);
const white = (width = 96, height = 96) => image(width, height, [255, 255, 255]);
const gray = (value = 128) => image(96, 96, [value, value, value]);
const checker = (width = 128, height = 128) => image(width, height,
  (x, y) => (x + y) % 2 ? [255, 255, 255] : [0, 0, 0]);
const smoothGradient = () => image(128, 96,
  (x) => { const v = Math.round(x * 255 / 127); return [v, v, v]; });
const codes = (report) => report.issues.map((issue) => issue.code);
const has = (report, issue) => codes(report).includes(issue);

function mockCanvasBrowser(t, { width = 256, height = 128, pixels = null, failDraw = false } = {}) {
  const oldDocument = globalThis.document;
  const oldCreateImageBitmap = globalThis.createImageBitmap;
  const calls = { close: 0, draw: 0, get: 0, canvas: null };
  globalThis.createImageBitmap = async (file) => ({
    width: file.width ?? width,
    height: file.height ?? height,
    close() { calls.close += 1; },
  });
  globalThis.document = {
    createElement(tag) {
      assert.equal(tag, 'canvas');
      const canvas = {
        width: 0,
        height: 0,
        getContext(kind) {
          assert.equal(kind, '2d');
          return {
            fillStyle: '',
            fillRect() {},
            drawImage() { calls.draw += 1; if (failDraw) throw new Error('draw error'); },
            getImageData(_x, _y, w, h) {
              calls.get += 1;
              // For dimensional testing use deterministic pixels matching the resized canvas.
              if (pixels && w === pixels.width && h === pixels.height) return pixels;
              return checker(w, h);
            },
          };
        },
      };
      calls.canvas = canvas;
      return canvas;
    },
  };
  t.after(() => {
    if (oldDocument === undefined) delete globalThis.document;
    else globalThis.document = oldDocument;
    if (oldCreateImageBitmap === undefined) delete globalThis.createImageBitmap;
    else globalThis.createImageBitmap = oldCreateImageBitmap;
  });
  return calls;
}

function fakeFile({ width = 128, height = 128, size = 1000, type = 'image/png', name = 'scene.png' } = {}) {
  return { width, height, size, type, name };
}

test('exposes a stable, immutable, dependency-free CommonJS API', () => {
  assert.equal(typeof quality.version, 'string');
  assert.equal(typeof quality.analyzeImageData, 'function');
  assert.equal(typeof quality.compareReports, 'function');
  assert.equal(typeof quality.analyzeFile, 'function');
  assert.equal(typeof quality.analyzeFiles, 'function');
  assert.equal(Object.isFrozen(quality), true);
  assert.equal(Object.isFrozen(quality.thresholds), true);
});

test('uniform black: warns of darkness and low contrast without inventing a focus diagnosis', () => {
  const report = quality.analyzeImageData(black());
  assert.equal(report.advisory_only, true);
  assert.equal(report.metrics.mean_luminance, 0);
  assert.ok(has(report, 'underexposed'));
  assert.ok(has(report, 'low_contrast'));
  assert.ok(!has(report, 'possible_blur'));
  assert.equal(report.recommendation, 'review_capture_conditions');
});

test('uniform white: warns of saturation and low contrast', () => {
  const report = quality.analyzeImageData(white());
  assert.equal(report.metrics.mean_luminance, 255);
  assert.ok(has(report, 'overexposed'));
  assert.ok(has(report, 'low_contrast'));
  assert.ok(!has(report, 'underexposed'));
});

test('mid-gray uniform image warns about low contrast, not exposure extremes', () => {
  const report = quality.analyzeImageData(gray(128));
  assert.deepEqual(codes(report), ['low_contrast']);
  assert.equal(report.metrics.robust_contrast, 0);
});

test('smooth gradient shows limited spatial detail (possible blur) with adequate contrast', () => {
  const report = quality.analyzeImageData(smoothGradient());
  assert.ok(report.metrics.robust_contrast > 45);
  assert.ok(has(report, 'possible_blur'));
  assert.ok(!has(report, 'low_contrast'));
});

test('sharp checkerboard avoids blur and contrast warnings', () => {
  const report = quality.analyzeImageData(checker());
  assert.deepEqual(report.issues, []);
  assert.equal(report.recommendation, 'proceed_with_visual_inspection');
  assert.ok(report.metrics.laplacian_variance > quality.thresholds.min_focus_variance);
});

test('tiny images do not trigger blur heuristic even when sharpness is low', () => {
  const report = quality.analyzeImageData(image(32, 32,
    (x) => { const v = Math.round(x * 255 / 31); return [v, v, v]; }));
  assert.ok(!has(report, 'possible_blur'));
});

test('transparent pixels are composited over white and unsigned Uint8Array is accepted', () => {
  const rgba = image(2, 1, [0, 0, 0, 0], { clamped: false });
  const report = quality.analyzeImageData(rgba);
  assert.equal(report.metrics.mean_luminance, 255);
  assert.equal(report.metrics.saturated_pixel_fraction, 1);
});

test('sampled statistics and warning objects have expected types', () => {
  const report = quality.analyzeImageData(black());
  assert.equal(report.dimensions.width, 96);
  assert.equal(report.dimensions.height, 96);
  assert.ok(Number.isFinite(report.metrics.laplacian_variance));
  for (const issue of report.issues) {
    assert.equal(issue.severity, 'warning');
    assert.equal(typeof issue.message, 'string');
    assert.ok(issue.recommendation.length > 0);
  }
});

test('rejects invalid image dimensions, pixel limit, and RGBA buffers', () => {
  assert.throws(() => quality.analyzeImageData(null), /imageData must be an object/);
  assert.throws(() => quality.analyzeImageData({width: 0, height: 1, data: new Uint8Array()}), /dimensions/);
  assert.throws(() => quality.analyzeImageData({width: 2.5, height: 2, data: new Uint8Array(16)}), /dimensions/);
  assert.throws(() => quality.analyzeImageData({width: 12_000_001, height: 1, data: new Uint8Array()}), /12-megapixel/);
  assert.throws(() => quality.analyzeImageData({width: 2, height: 2, data: new Uint8Array(15)}), /RGBA byte length/);
  assert.throws(() => quality.analyzeImageData({width: 1, height: 1, data: [0, 0, 0, 255]}), /Uint8Array/);
  assert.throws(() => quality.analyzeImageData(checker(16, 16), []), /options must be an object/);
});

test('compareReports: unchanged image-quality metrics produce no warnings', () => {
  const report = quality.analyzeImageData(checker());
  const comparison = quality.compareReports({ reference: report, observed: report });
  assert.equal(comparison.advisory_only, true);
  assert.equal(comparison.pair_metrics.length, 1);
  assert.equal(comparison.pair_metrics[0].same_dimensions, true);
  assert.equal(comparison.pair_metrics[0].possible_lighting_shift, false);
  assert.equal(comparison.recommendation, 'proceed_with_visual_inspection');
});

test('compareReports detects a substantial brightness shift without claiming scene change', () => {
  const reference = quality.analyzeImageData(checker());
  const observed = quality.analyzeImageData(white(128, 128));
  const comparison = quality.compareReports({reference, observed});
  assert.ok(has(comparison, 'possible_lighting_shift'));
  assert.equal(comparison.pair_metrics[0].possible_lighting_shift, true);
  assert.equal(comparison.recommendation, 'review_capture_conditions');
  assert.equal(comparison.advisory_only, true);
});

test('compareReports checks original dimensions even when sampled sizes match', () => {
  const base = quality.analyzeImageData(checker());
  const reference = {...base, source_dimensions: {width: 1280, height: 720}};
  const observed = {...base, source_dimensions: {width: 1920, height: 1080}};
  const comparison = quality.compareReports({reference, observed});
  assert.ok(has(comparison, 'dimension_mismatch'));
  assert.equal(comparison.pair_metrics[0].same_dimensions, false);
});

test('compareReports checks followup against the original reference and labels all pairs', () => {
  const base = quality.analyzeImageData(checker());
  const comparison = quality.compareReports({reference: base, observed: base, followup: base});
  assert.deepEqual(comparison.pair_metrics.map((p) => p.pair),
    ['reference_to_observed', 'reference_to_followup']);
  assert.deepEqual(comparison.issues, []);
});

test('compareReports attributes per-image quality advisories to their image roles', () => {
  const reference = quality.analyzeImageData(black());
  const observed = quality.analyzeImageData(checker(96, 96));
  const comparison = quality.compareReports({reference, observed});
  assert.ok(comparison.issues.some((i) => i.code === 'underexposed' && i.role === 'reference'));
  assert.ok(comparison.issues.some((i) => i.code === 'possible_lighting_shift' && i.role === undefined));
});

test('compareReports rejects missing or malformed reports', () => {
  const base = quality.analyzeImageData(checker());
  assert.throws(() => quality.compareReports(null), /reports must be an object/);
  assert.throws(() => quality.compareReports({reference: base}), /observed is not a valid/);
  assert.throws(() => quality.compareReports({reference: base, observed: {}}), /observed is not a valid/);
});

test('analyzeFile validates input sizes, MIME and sampling configuration', async () => {
  await assert.rejects(quality.analyzeFile(null), /expects a File or Blob/);
  await assert.rejects(quality.analyzeFile(fakeFile({size: 0})), /between 1 byte and 10 MiB/);
  await assert.rejects(quality.analyzeFile(fakeFile({size: 10 * 1024 * 1024 + 1})), /between 1 byte and 10 MiB/);
  await assert.rejects(quality.analyzeFile(fakeFile({type: 'text/plain'})), /unsupported image MIME/);
  await assert.rejects(quality.analyzeFile(fakeFile(), {maxDimension: 40}), /maxDimension/);
  await assert.rejects(quality.analyzeFile(fakeFile(), {maxDimension: 1025}), /maxDimension/);
});

test('analyzeFile requires browser Canvas when called from plain Node.js', async () => {
  await assert.rejects(quality.analyzeFile(fakeFile()), /browser with Canvas support/);
});

test('analyzeFile: fake browser decodes, downsamples, and releases the image bitmap', async (t) => {
  const calls = mockCanvasBrowser(t, {width: 512, height: 256});
  const report = await quality.analyzeFile(fakeFile({width: 512, height: 256, name: 'capture.png'}),
    {maxDimension: 256});
  assert.deepEqual(report.source_dimensions, {width: 512, height: 256});
  assert.deepEqual(report.sampled_dimensions, {width: 256, height: 128});
  assert.equal(report.file_name, 'capture.png');
  assert.equal(calls.canvas.width, 256);
  assert.equal(calls.canvas.height, 128);
  assert.equal(calls.draw, 1);
  assert.equal(calls.get, 1);
  assert.equal(calls.close, 1);
});

test('analyzeFile closes decoded bitmap when canvas fails', async (t) => {
  const calls = mockCanvasBrowser(t, {failDraw: true});
  await assert.rejects(quality.analyzeFile(fakeFile()), /draw error/);
  assert.equal(calls.close, 1);
});

test('analyzeFile rejects oversized decoded image dimensions', async (t) => {
  const calls = mockCanvasBrowser(t, {width: 5000, height: 3000});
  await assert.rejects(quality.analyzeFile(fakeFile({width: 5000, height: 3000})), /12 megapixels/);
  assert.equal(calls.close, 1);
});

test('analyzeFiles handles two or three images without changing the engine decision', async (t) => {
  mockCanvasBrowser(t);
  const input = {reference: fakeFile({name: 'reference.png'}), observed: fakeFile({name: 'observed.png'})};
  const two = await quality.analyzeFiles(input);
  assert.equal(two.engine_decision_unchanged, true);
  assert.equal(two.advisory_only, true);
  assert.deepEqual(Object.keys(two.reports), ['reference', 'observed']);
  assert.equal(two.comparison.pair_metrics.length, 1);
  const three = await quality.analyzeFiles({...input, followup: fakeFile({name: 'followup.png'})});
  assert.deepEqual(Object.keys(three.reports), ['reference', 'observed', 'followup']);
  assert.equal(three.comparison.pair_metrics.length, 2);
  assert.equal(three.engine_decision_unchanged, true);
});

test('analyzeFiles rejects a missing reference or observation', async () => {
  await assert.rejects(quality.analyzeFiles({}), /reference and observed files are required/);
  await assert.rejects(quality.analyzeFiles({reference: fakeFile()}), /reference and observed files are required/);
});
