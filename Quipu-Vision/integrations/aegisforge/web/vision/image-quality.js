/**
 * Quipu Vision — advisory image-quality inspection (browser-only).
 *
 * Intended destination:
 *   Quipu-Vision/integrations/aegisforge/web/vision/image-quality.js
 * Production mirror:
 *   AegisForge_agent/src/aegisforge/web/vision/image-quality.js
 *
 * This file assesses image acquisition quality, NOT visual scene changes.
 * Results are heuristics, not calibrated confidence scores or safety decisions.
 * The existing OpenCV 5 engine and A2A inspect/verify workflow remain authoritative.
 *
 * Public API (window.QuipuVisionImageQuality):
 *   analyzeImageData({width, height, data}) -> report
 *   compareReports({reference, observed, followup?}) -> comparison
 *   analyzeFile(file) -> Promise<report>
 *   analyzeFiles({reference: File, observed: File, followup?: File})
 *     -> Promise<{reports, comparison, ...}>
 *
 * In Node.js, the same pure functions are available through require().
 * To activate in the browser, explicitly serve this file and load it BEFORE
 * app.js with a same-origin script tag. Merely adding the file does not enable it.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root && typeof root.document !== "undefined") {
    root.QuipuVisionImageQuality = api;
  }
})(typeof globalThis === "object" ? globalThis : undefined, function () {
  "use strict";

  const VERSION = "0.1.0";
  const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
  const MAX_IMAGE_PIXELS = 12_000_000;
  const MAX_DIMENSION = 384; // Downsample for bounded, responsive client-side analysis.
  const SUPPORTED_MIME = Object.freeze([
    "image/png", "image/jpeg", "image/bmp", "image/webp",
  ]);
  const ROLES = Object.freeze(["reference", "observed", "followup"]);
  const THRESHOLDS = Object.freeze({
    dark_luminance: 18,
    dark_mean_luminance: 52,
    dark_pixel_fraction: 0.5,
    saturated_luminance: 250,
    saturated_mean_luminance: 165,
    saturated_pixel_fraction: 0.2,
    min_robust_contrast: 45, // p95 - p05; approximate 8-bit luma units.
    min_focus_variance: 55, // Discrete 4-neighbor Laplacian; heuristic only.
    brightness_shift: 32, // Absolute mean luma difference between captures.
    min_focus_dimension: 64,
  });

  function fail(message) {
    throw new TypeError(`Quipu Vision image quality: ${message}`);
  }

  function rounded(value, decimals = 3) {
    return Math.round(value * (10 ** decimals)) / (10 ** decimals);
  }

  function advisory(code, message, recommendation) {
    return { code, severity: "warning", message, recommendation };
  }

  function percentileFromHistogram(histogram, total, quantile) {
    const target = Math.max(1, Math.ceil(total * quantile));
    let count = 0;
    for (let value = 0; value < histogram.length; value += 1) {
      count += histogram[value];
      if (count >= target) return value;
    }
    return 255;
  }

  function focusVariance(luminance, width, height) {
    if (width < 3 || height < 3) return null;
    let sum = 0;
    let sumSquares = 0;
    let samples = 0;
    for (let y = 1; y < height - 1; y += 1) {
      const row = y * width;
      for (let x = 1; x < width - 1; x += 1) {
        const i = row + x;
        const edge = 4 * luminance[i] - luminance[i - 1] -
          luminance[i + 1] - luminance[i - width] - luminance[i + width];
        sum += edge;
        sumSquares += edge * edge;
        samples += 1;
      }
    }
    if (!samples) return null;
    const average = sum / samples;
    return Math.max(0, sumSquares / samples - average * average);
  }

  function analyzeImageData(imageData, options = {}) {
    if (!imageData || typeof imageData !== "object") fail("imageData must be an object");
    const { width, height, data } = imageData;
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1) {
      fail("image dimensions must be positive integers");
    }
    if (width * height > MAX_IMAGE_PIXELS) fail("image exceeds the 12-megapixel limit");
    if (!(data instanceof Uint8Array || data instanceof Uint8ClampedArray)) {
      fail("image data must be an RGBA Uint8Array or Uint8ClampedArray");
    }
    if (data.length !== width * height * 4) fail("RGBA byte length differs from image dimensions");
    if (options && (typeof options !== "object" || Array.isArray(options))) {
      fail("options must be an object");
    }

    const pixels = width * height;
    const luminance = new Float32Array(pixels);
    const histogram = new Uint32Array(256);
    let total = 0;
    let dark = 0;
    let saturated = 0;
    for (let i = 0; i < pixels; i += 1) {
      const offset = i * 4;
      const alpha = data[offset + 3] / 255;
      // Transparent pixels are treated as white, matching the canvas decode.
      const red = data[offset] * alpha + 255 * (1 - alpha);
      const green = data[offset + 1] * alpha + 255 * (1 - alpha);
      const blue = data[offset + 2] * alpha + 255 * (1 - alpha);
      const y = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      luminance[i] = y;
      total += y;
      histogram[Math.min(255, Math.max(0, Math.round(y)))] += 1;
      if (y <= THRESHOLDS.dark_luminance) dark += 1;
      if (y >= THRESHOLDS.saturated_luminance) saturated += 1;
    }
    const mean = total / pixels;
    const p05 = percentileFromHistogram(histogram, pixels, 0.05);
    const p95 = percentileFromHistogram(histogram, pixels, 0.95);
    const contrast = p95 - p05;
    const blurMetric = focusVariance(luminance, width, height);
    const darkFraction = dark / pixels;
    const saturatedFraction = saturated / pixels;
    const issues = [];

    if (mean < THRESHOLDS.dark_mean_luminance &&
        darkFraction >= THRESHOLDS.dark_pixel_fraction) {
      issues.push(advisory(
        "underexposed", "Much of the image appears very dark.",
        "If possible, recapture with more even illumination."
      ));
    }
    if (mean > THRESHOLDS.saturated_mean_luminance &&
        saturatedFraction >= THRESHOLDS.saturated_pixel_fraction) {
      issues.push(advisory(
        "overexposed", "A substantial part of the image appears saturated.",
        "Reduce glare or exposure and capture a new observation."
      ));
    }
    if (contrast < THRESHOLDS.min_robust_contrast) {
      issues.push(advisory(
        "low_contrast", "The image has a limited luminance range.",
        "Use more uniform lighting; low contrast may obscure small changes."
      ));
    }
    if (width >= THRESHOLDS.min_focus_dimension &&
        height >= THRESHOLDS.min_focus_dimension &&
        contrast >= THRESHOLDS.min_robust_contrast &&
        blurMetric !== null && blurMetric < THRESHOLDS.min_focus_variance) {
      issues.push(advisory(
        "possible_blur", "Fine detail may be limited (Laplacian-based sharpness heuristic).",
        "Check focus and motion blur before relying on this observation."
      ));
    }

    return {
      analyzer_version: VERSION,
      advisory_only: true,
      dimensions: { width, height },
      metrics: {
        mean_luminance: rounded(mean, 2),
        percentile_05_luminance: p05,
        percentile_95_luminance: p95,
        robust_contrast: contrast,
        dark_pixel_fraction: rounded(darkFraction, 4),
        saturated_pixel_fraction: rounded(saturatedFraction, 4),
        laplacian_variance: blurMetric === null ? null : rounded(blurMetric, 2),
      },
      issues,
      recommendation: issues.length ? "review_capture_conditions" : "proceed_with_visual_inspection",
    };
  }

  function validateReport(report, role) {
    if (!report || !report.dimensions || !report.metrics || !Array.isArray(report.issues) ||
        !Number.isFinite(report.metrics.mean_luminance) ||
        !Number.isSafeInteger(report.dimensions.width) ||
        !Number.isSafeInteger(report.dimensions.height)) {
      fail(`${role} is not a valid image-quality report`);
    }
  }

  function compareReports(reports) {
    if (!reports || typeof reports !== "object") fail("reports must be an object");
    const { reference, observed, followup } = reports;
    validateReport(reference, "reference");
    validateReport(observed, "observed");
    if (followup !== undefined && followup !== null) validateReport(followup, "followup");

    const available = { reference, observed };
    if (followup !== undefined && followup !== null) available.followup = followup;
    const pairs = [["reference", "observed"]];
    if (available.followup) pairs.push(["reference", "followup"]);
    const pair_metrics = [];
    const issues = [];
    for (const [baselineName, observedName] of pairs) {
      const first = available[baselineName];
      const second = available[observedName];
      // Sampled canvases may have the same size despite differing source sizes.
      // Prefer original file dimensions when reports came from analyzeFile().
      const firstSize = first.source_dimensions || first.dimensions;
      const secondSize = second.source_dimensions || second.dimensions;
      const dimensionMismatch = firstSize.width !== secondSize.width ||
        firstSize.height !== secondSize.height;
      if (dimensionMismatch) {
        issues.push(advisory(
          "dimension_mismatch", `${baselineName} and ${observedName} have different dimensions.`,
          "Use images with matching dimensions; OpenCV's fixed-view comparison requires this."
        ));
      }
      const change = second.metrics.mean_luminance - first.metrics.mean_luminance;
      const lightingShift = Math.abs(change) >= THRESHOLDS.brightness_shift;
      pair_metrics.push({
        pair: `${baselineName}_to_${observedName}`,
        mean_luminance_difference: rounded(change, 2),
        possible_lighting_shift: lightingShift,
        same_dimensions: !dimensionMismatch,
      });
      if (lightingShift) {
        issues.push(advisory(
          "possible_lighting_shift",
          `Mean image brightness differs substantially between ${baselineName} and ${observedName}.`,
          "Check lighting conditions before interpreting pixel differences as scene changes."
        ));
      }
    }
    for (const [role, report] of Object.entries(available)) {
      for (const issue of report.issues) {
        issues.push({ ...issue, role });
      }
    }
    return {
      analyzer_version: VERSION,
      advisory_only: true,
      pair_metrics,
      issues,
      recommendation: issues.length ? "review_capture_conditions" : "proceed_with_visual_inspection",
    };
  }

  async function decodeFile(file, maxDimension) {
    if (typeof document === "undefined" || !document.createElement) {
      fail("analyzeFile requires a browser with Canvas support");
    }
    let image;
    let url;
    let bitmap;
    try {
      if (typeof createImageBitmap === "function") {
        try {
          bitmap = await createImageBitmap(file);
          image = bitmap;
        } catch {
          // Browser decoder may not support some valid image formats.
        }
      }
      if (!image) {
        if (typeof Image === "undefined" || typeof URL === "undefined") {
          fail("browser cannot decode this image format");
        }
        url = URL.createObjectURL(file);
        image = new Image();
        image.src = url;
        if (typeof image.decode === "function") {
          await image.decode();
        } else {
          await new Promise((resolve, reject) => {
            image.onload = resolve;
            image.onerror = reject;
            if (image.complete) {
              if (image.naturalWidth) resolve(); else reject(new Error("Image decoding failed"));
            }
          });
        }
      }
      const sourceWidth = image.naturalWidth || image.width;
      const sourceHeight = image.naturalHeight || image.height;
      if (!sourceWidth || !sourceHeight || sourceWidth * sourceHeight > MAX_IMAGE_PIXELS) {
        fail("image has invalid dimensions or exceeds 12 megapixels");
      }
      const scale = Math.min(1, maxDimension / Math.max(sourceWidth, sourceHeight));
      const width = Math.max(1, Math.round(sourceWidth * scale));
      const height = Math.max(1, Math.round(sourceHeight * scale));
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) fail("2D Canvas is unavailable");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(image, 0, 0, width, height);
      return { imageData: ctx.getImageData(0, 0, width, height), sourceWidth, sourceHeight };
    } finally {
      if (bitmap && typeof bitmap.close === "function") bitmap.close();
      if (url) URL.revokeObjectURL(url);
    }
  }

  async function analyzeFile(file, options = {}) {
    if (!file || typeof file !== "object" || typeof file.size !== "number") {
      fail("analyzeFile expects a File or Blob");
    }
    if (!Number.isFinite(file.size) || file.size <= 0 || file.size > MAX_IMAGE_BYTES) {
      fail("each image must be between 1 byte and 10 MiB");
    }
    const mime = String(file.type || "").toLowerCase();
    if (mime && !SUPPORTED_MIME.includes(mime)) fail("unsupported image MIME type");
    const maxDimension = options.maxDimension === undefined ? MAX_DIMENSION : options.maxDimension;
    if (!Number.isSafeInteger(maxDimension) || maxDimension < 64 || maxDimension > 1024) {
      fail("maxDimension must be an integer between 64 and 1024");
    }
    const decoded = await decodeFile(file, maxDimension);
    const report = analyzeImageData(decoded.imageData);
    return {
      ...report,
      source_dimensions: { width: decoded.sourceWidth, height: decoded.sourceHeight },
      sampled_dimensions: report.dimensions,
      file_name: typeof file.name === "string" ? file.name : null,
    };
  }

  async function analyzeFiles(files, options = {}) {
    if (!files || typeof files !== "object" || !files.reference || !files.observed) {
      fail("reference and observed files are required");
    }
    const reports = {};
    let totalBytes = 0;
    for (const role of ROLES) {
      const file = files[role];
      if (!file) continue;
      totalBytes += file.size;
      if (!Number.isFinite(totalBytes) || totalBytes > 32 * 1024 * 1024) {
        fail("combined image size exceeds the 32 MiB browser limit");
      }
      reports[role] = await analyzeFile(file, options);
    }
    const comparison = compareReports(reports);
    return {
      analyzer_version: VERSION,
      advisory_only: true,
      reports,
      comparison,
      recommendation: comparison.recommendation,
      // This value MUST NOT be used to change A2A inspect/verify decisions.
      engine_decision_unchanged: true,
    };
  }

  return Object.freeze({
    version: VERSION,
    thresholds: THRESHOLDS,
    analyzeImageData,
    compareReports,
    analyzeFile,
    analyzeFiles,
  });
});
