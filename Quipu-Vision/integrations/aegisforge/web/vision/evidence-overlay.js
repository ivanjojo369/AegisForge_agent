/**
 * Quipu Vision — evidence-only image overlay (0.5 candidate).
 *
 * Distribution location:
 *   Quipu-Vision/integrations/aegisforge/web/vision/evidence-overlay.js
 * Runtime location after integration:
 *   AegisForge_agent/src/aegisforge/web/vision/evidence-overlay.js
 *
 * Receives the *actual* A2A evidence returned by OpenCV 5. Does not detect
 * objects, recompute differences, infer confidence, match object identities,
 * or influence inspect/verify decisions. No network requests or dependencies.
 *
 * Browser usage (AFTER a2a_server.py registers /demo/vision/evidence-overlay.js):
 *   <script src="/demo/vision/evidence-overlay.js" defer></script>
 *   const evidence = QuipuVisionEvidenceOverlay.extractEvidence(artifact);
 *   QuipuVisionEvidenceOverlay.drawOverlay({
 *     canvas: observedCanvas, image: document.getElementById('observed-image'),
 *     ...evidence.observed, stage: 'observed'
 *   });
 *   // For follow-up, use evidence.followup ONLY when it is not null.
 *
 * Can also be used in Node.js tests with require('.../evidence-overlay.js').
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module && module.exports) module.exports = api;
  else if (root) root.QuipuVisionEvidenceOverlay = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const VERSION = '0.5.0-preview';
  const MAX_REGIONS = 12; // Mirrors current Python core's public top-12 regions.
  const MAX_SOURCE_PIXELS = 12_000_000; // Python core limit.
  const MAX_RENDER_PIXELS = 1_048_576;
  const STAGES = new Set(['observed', 'followup']);
  const COLOR = Object.freeze({
    observed: '#50dbd4',
    followup: '#ffc57b',
    roi: '#f0f7ff',
    text: '#09111e',
  });

  function isRecord(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function dimensions(image) {
    if (!image || typeof image !== 'object') throw new TypeError('Image source is required');
    const width = image.naturalWidth || image.width;
    const height = image.naturalHeight || image.height;
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) ||
        width <= 0 || height <= 0 || width * height > MAX_SOURCE_PIXELS) {
      throw new RangeError('Image must be loaded, have valid dimensions and be at most 12 MP');
    }
    return { width, height };
  }

  function integerBox(box, sourceWidth, sourceHeight) {
    if (!Array.isArray(box) || box.length !== 4 ||
        !box.every(Number.isSafeInteger)) return null;
    const [x, y, width, height] = box;
    if (x < 0 || y < 0 || width <= 0 || height <= 0 ||
        x + width > sourceWidth || y + height > sourceHeight) return null;
    return { x, y, width, height };
  }

  function canvasSize(sourceWidth, sourceHeight, maxWidth, maxHeight) {
    for (const [value, name] of [[maxWidth, 'maxWidth'], [maxHeight, 'maxHeight']]) {
      if (!Number.isSafeInteger(value) || value < 64 || value > 4096) {
        throw new RangeError(`${name} must be an integer between 64 and 4096`);
      }
    }
    const scale = Math.min(
      1, maxWidth / sourceWidth, maxHeight / sourceHeight,
      Math.sqrt(MAX_RENDER_PIXELS / (sourceWidth * sourceHeight))
    );
    return {
      width: Math.max(1, Math.floor(sourceWidth * scale)),
      height: Math.max(1, Math.floor(sourceHeight * scale)),
    };
  }

  /**
   * Extracts *separate* observed and follow-up regions from the existing A2A
   * PluginResult or its output.results[0] item. Follow-up regions are taken
   * only from verification.observation, never guessed from the first image.
   */
  function extractEvidence(artifact) {
    if (!isRecord(artifact)) throw new TypeError('Expected an A2A artifact or result object');
    const output = isRecord(artifact.output) ? artifact.output : artifact;
    const item = Array.isArray(output.results) ? output.results[0] : output;
    if (!isRecord(item)) throw new TypeError('Missing Quipu Vision result case');
    const evidence = isRecord(item.evidence) ? item.evidence
      : (isRecord(output.evidence) ? output.evidence : null);
    const observed = evidence && isRecord(evidence.observation) ? evidence.observation : null;
    const verification = evidence && isRecord(evidence.verification) ? evidence.verification : null;
    const last = verification && isRecord(verification.observation) ? verification.observation : null;

    const firstRegions = Array.isArray(item.regions) ? item.regions
      : (observed && Array.isArray(observed.regions) ? observed.regions : []);
    const firstTotal = Number.isSafeInteger(item.regions_total) && item.regions_total >= firstRegions.length
      ? item.regions_total : (observed && Number.isSafeInteger(observed.regions_total)
        ? observed.regions_total : firstRegions.length);
    const didProcessFollowup = Boolean(last && item.followup_processed !== false);
    return {
      observed: {
        regions: firstRegions,
        roi: observed ? observed.roi_xywh ?? null : null,
        regionsTotal: firstTotal,
      },
      followup: didProcessFollowup ? {
        regions: Array.isArray(last.regions) ? last.regions : [],
        roi: last.roi_xywh ?? null,
        regionsTotal: Number.isSafeInteger(last.regions_total)
          ? last.regions_total : (Array.isArray(last.regions) ? last.regions.length : 0),
      } : null,
      nextAction: typeof item.next_action === 'string' ? item.next_action : null,
      // Metadata only: drawing never treats this as object/safety confidence.
      verifiedPixelPersistence: didProcessFollowup && item.verified === true,
    };
  }

  function regionBox(region, width, height) {
    return isRecord(region) ? integerBox(region.bbox_xywh, width, height) : null;
  }

  function caption(ctx, number, x, y, color, width, height) {
    const label = `#${number}`;
    const size = 14;
    const boxWidth = 37;
    const boxHeight = 20;
    const left = Math.min(Math.max(0, x), Math.max(0, width - boxWidth));
    const top = Math.min(Math.max(0, y - boxHeight), Math.max(0, height - boxHeight));
    ctx.fillStyle = color;
    ctx.fillRect(left, top, boxWidth, boxHeight);
    ctx.fillStyle = COLOR.text;
    ctx.font = `bold ${size}px system-ui, sans-serif`;
    ctx.textBaseline = 'middle';
    ctx.fillText(label, left + 5, top + boxHeight / 2);
  }

  /**
   * Render accurately scaled bounding boxes on a separate canvas; it never
   * edits the supplied image. Invalid/out-of-bounds regions are skipped.
   * All geometry stays in the coordinates of the full ORIGINAL image.
   */
  function drawOverlay({
    canvas, image, regions = [], roi = null, stage = 'observed',
    maxWidth = 1024, maxHeight = 768, showLabels = true,
  } = {}) {
    if (!canvas || typeof canvas.getContext !== 'function') {
      throw new TypeError('A canvas with a 2D context is required');
    }
    if (!STAGES.has(stage)) throw new RangeError('stage must be observed or followup');
    if (!Array.isArray(regions)) throw new TypeError('regions must be an array');
    if (typeof showLabels !== 'boolean') throw new TypeError('showLabels must be boolean');

    const source = dimensions(image);
    const size = canvasSize(source.width, source.height, maxWidth, maxHeight);
    // Setting width/height clears old drawings and resets Canvas context state.
    canvas.width = size.width;
    canvas.height = size.height;
    const ctx = canvas.getContext('2d');
    if (!ctx || typeof ctx.drawImage !== 'function' || typeof ctx.strokeRect !== 'function') {
      throw new Error('Canvas 2D drawing is unavailable');
    }
    const sx = size.width / source.width;
    const sy = size.height / source.height;
    ctx.drawImage(image, 0, 0, size.width, size.height);

    let roiDrawn = false;
    if (roi !== null) {
      const box = integerBox(roi, source.width, source.height);
      if (!box) throw new RangeError('roi must be a rectangle within the source image');
      ctx.strokeStyle = COLOR.roi;
      ctx.lineWidth = 1.5;
      if (typeof ctx.setLineDash === 'function') ctx.setLineDash([6, 4]);
      ctx.strokeRect(box.x * sx, box.y * sy, box.width * sx, box.height * sy);
      if (typeof ctx.setLineDash === 'function') ctx.setLineDash([]);
      roiDrawn = true;
    }

    const displayed = regions.slice(0, MAX_REGIONS);
    const color = COLOR[stage];
    let drawn = 0;
    let skipped = 0;
    displayed.forEach((region, index) => {
      const box = regionBox(region, source.width, source.height);
      if (!box) { skipped += 1; return; }
      const x = box.x * sx;
      const y = box.y * sy;
      const w = box.width * sx;
      const h = box.height * sy;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.strokeRect(x, y, Math.max(1, w), Math.max(1, h));
      if (showLabels && typeof ctx.fillRect === 'function' && typeof ctx.fillText === 'function') {
        caption(ctx, index + 1, x, y, color, size.width, size.height);
      }
      drawn += 1;
    });
    if (typeof canvas.setAttribute === 'function') {
      canvas.setAttribute('role', 'img');
      canvas.setAttribute('aria-label',
        `${stage === 'observed' ? 'Observed image' : 'Follow-up image'}: ${drawn} OpenCV detected region(s) shown. ` +
        'Colored rectangles show pixel differences, not object identity or safety.');
    }
    return {
      stage,
      sourceWidth: source.width, sourceHeight: source.height,
      canvasWidth: size.width, canvasHeight: size.height,
      drawnRegions: drawn, invalidRegions: skipped,
      omittedRegions: Math.max(0, regions.length - MAX_REGIONS),
      roiDrawn,
      sourceCoordinatesPreserved: true,
      inferencePerformed: false,
    };
  }

  /** Draw both observations without inventing follow-up regions. */
  function drawFromArtifact({ artifact, observed, followup = null } = {}) {
    if (!isRecord(observed)) throw new TypeError('observed must contain {canvas, image}');
    const evidence = extractEvidence(artifact);
    const drawnObserved = drawOverlay({ ...observed, ...evidence.observed, stage: 'observed' });
    let drawnFollowup = null;
    if (isRecord(followup) && evidence.followup) {
      drawnFollowup = drawOverlay({ ...followup, ...evidence.followup, stage: 'followup' });
    } else if (isRecord(followup) && followup.canvas) {
      // Clear old results when the new task has no processed follow-up.
      followup.canvas.width = 0;
      followup.canvas.height = 0;
      if (typeof followup.canvas.setAttribute === 'function') {
        followup.canvas.setAttribute('aria-label', 'No processed follow-up evidence available');
      }
    }
    return { observed: drawnObserved, followup: drawnFollowup, evidence };
  }

  return Object.freeze({
    version: VERSION,
    extractEvidence,
    drawOverlay,
    drawFromArtifact,
  });
});
