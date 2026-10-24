/**
 * Quipu Vision 0.4 — local-first A2A browser client.
 *
 * Install alongside index.html at src/aegisforge/web/vision/app.js.
 * Requires the existing AegisForge A2A server to serve this file at
 * /demo/vision/app.js and to expose /vision/capabilities and POST /.
 * No third-party libraries, camera access, remote URLs, or credentials.
 */
(() => {
  "use strict";

  const ENDPOINTS = Object.freeze({ capabilities: "/vision/capabilities", a2a: "/" });
  const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
  const MAX_TOTAL_BYTES = 32 * 1024 * 1024;
  const REQUEST_TIMEOUT_MS = 90_000;
  const POLL_INTERVAL_MS = 800;
  const MAX_TASK_POLLS = 30;
  const MIME_BY_EXTENSION = Object.freeze({
    png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
    bmp: "image/bmp", webp: "image/webp",
  });
  const EXTENSION_BY_MIME = Object.freeze({
    "image/png": "png", "image/jpeg": "jpg", "image/bmp": "bmp", "image/webp": "webp",
  });
  const imageFields = [
    { role: "reference", input: "reference-file", preview: "reference-preview", image: "reference-image", name: "reference-name" },
    { role: "observed", input: "observed-file", preview: "observed-preview", image: "observed-image", name: "observed-name" },
    { role: "followup", input: "followup-file", preview: "followup-preview", image: "followup-image", name: "followup-name" },
  ];

  const $ = (id) => document.getElementById(id);
  const elements = {
    form: $("vision-form"), run: $("run-button"), reset: $("reset-button"),
    feedback: $("form-feedback"), serverStatus: $("server-status"), cv: $("opencv-version"),
    analysisStatus: $("analysis-status"), empty: $("empty-results"),
    results: $("results-content"), error: $("error-panel"), errorMessage: $("error-message"),
    nextAction: $("next-action"), summary: $("result-summary"), change: $("change-detected"),
    verified: $("verified"), fraction: $("changed-fraction"), milliseconds: $("processing-ms"),
    regionCount: $("region-count"), regionsEmpty: $("regions-empty"),
    regionsWrap: $("regions-table-wrap"), regionsBody: $("regions-table-body"),
    events: $("events-list"), raw: $("raw-json"), threshold: $("threshold"),
    minFraction: $("min-region-fraction"),
  };
  const previews = new Map();
  let ready = false;
  let busy = false;
  let requestController = null;

  function setFeedback(message, isError = false) {
    elements.feedback.textContent = message;
    elements.feedback.classList.toggle("is-error", isError);
  }

  function updateButton() {
    const validInputs = Boolean($("reference-file").files[0] && $("observed-file").files[0]);
    elements.run.disabled = !ready || busy || !validInputs;
    elements.reset.disabled = busy;
    elements.run.textContent = busy ? "Inspecting…" : "Run visual inspection";
  }

  function showError(message) {
    elements.error.hidden = false;
    elements.errorMessage.textContent = message;
    elements.analysisStatus.textContent = "Failed";
    setFeedback(message, true);
  }

  function clearResult() {
    elements.results.hidden = true;
    elements.empty.hidden = false;
    elements.error.hidden = true;
    elements.errorMessage.textContent = "";
    elements.analysisStatus.textContent = "Not started";
    elements.raw.textContent = "";
    elements.regionsBody.replaceChildren();
    elements.events.replaceChildren();
  }

  function guessedMime(file) {
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    const expected = MIME_BY_EXTENSION[ext];
    if (!expected) throw new Error("Only PNG, JPEG, BMP and WebP images are supported.");
    const actual = (file.type || "").toLowerCase();
    if (actual && actual !== expected) {
      throw new Error(`${file.name}: file extension and reported image type do not match.`);
    }
    return expected;
  }

  function validateFile(file) {
    if (!file) return;
    guessedMime(file);
    if (file.size === 0 || file.size > MAX_IMAGE_BYTES) {
      throw new Error(`${file.name}: image must be between 1 byte and 10 MiB.`);
    }
  }

  function selectedImages() {
    const reference = $("reference-file").files[0];
    const observed = $("observed-file").files[0];
    const followup = $("followup-file").files[0];
    if (!reference || !observed) throw new Error("Select a reference image and an observed image.");
    const items = [
      { role: "reference", file: reference },
      { role: "observed", file: observed },
      ...(followup ? [{ role: "followup", file: followup }] : []),
    ];
    let size = 0;
    for (const item of items) {
      validateFile(item.file);
      size += item.file.size;
    }
    if (size > MAX_TOTAL_BYTES) throw new Error("The images exceed the 32 MiB combined upload limit.");
    return items;
  }

  async function imageDimensions(file) {
    const imageUrl = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.src = imageUrl;
      await image.decode();
      if (!image.naturalWidth || !image.naturalHeight) throw new Error("Empty image dimensions");
      return `${image.naturalWidth}x${image.naturalHeight}`;
    } catch {
      throw new Error(`${file.name}: cannot decode this image in the browser.`);
    } finally {
      URL.revokeObjectURL(imageUrl);
    }
  }

  async function validateDimensions(items) {
    const dimensions = await Promise.all(items.map((item) => imageDimensions(item.file)));
    if (new Set(dimensions).size > 1) {
      throw new Error("All selected images must have the same width and height.");
    }
  }

  function readImageBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error(`Unable to read ${file.name}.`));
      reader.onabort = () => reject(new Error("Image reading was cancelled."));
      reader.onload = () => {
        const result = reader.result;
        if (typeof result !== "string" || !result.includes(",")) {
          reject(new Error(`Invalid image data from ${file.name}.`));
          return;
        }
        resolve(result.slice(result.indexOf(",") + 1));
      };
      reader.readAsDataURL(file);
    });
  }

  function optionalNumber(input, { integer, min, max, label }) {
    const raw = input.value.trim();
    if (!raw) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
      throw new Error(`${label} must be ${integer ? "a whole number" : "a number"} between ${min} and ${max}.`);
    }
    return value;
  }

  function makeId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
    return `quipu-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  async function buildRequest(items) {
    const hasFollowup = items.some((item) => item.role === "followup");
    const data = { capability: "quipu_vision", operation: hasFollowup ? "verify" : "inspect" };
    const threshold = optionalNumber(elements.threshold, {
      integer: true, min: 1, max: 255, label: "Pixel difference threshold",
    });
    const regionFraction = optionalNumber(elements.minFraction, {
      integer: false, min: 0.000001, max: 0.5, label: "Minimum region fraction",
    });
    if (threshold !== null) data.threshold = threshold;
    if (regionFraction !== null) data.min_region_fraction = regionFraction;

    const parts = [{ kind: "data", data }];
    for (const { role, file } of items) {
      const mimeType = guessedMime(file);
      const encoded = await readImageBase64(file);
      parts.push({
        kind: "file",
        file: { name: `${role}.${EXTENSION_BY_MIME[mimeType]}`, mimeType, bytes: encoded },
      });
    }
    return {
      jsonrpc: "2.0", id: makeId(), method: "message/send",
      params: { message: { kind: "message", role: "user", messageId: makeId(), parts } },
    };
  }

  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store", credentials: "same-origin", ...options,
    });
    const mime = response.headers.get("content-type") || "";
    if (!mime.includes("application/json")) {
      throw new Error(`Server returned HTTP ${response.status} instead of JSON. Check the A2A route.`);
    }
    const body = await response.json();
    if (!response.ok) {
      throw new Error(`Server error (HTTP ${response.status}). ${messageFromError(body)}`.trim());
    }
    return body;
  }

  function messageFromError(obj) {
    if (typeof obj === "string") return obj;
    if (!obj || typeof obj !== "object") return "";
    const error = obj.error || obj.output?.error;
    if (typeof error === "string") return error;
    if (error && typeof error.message === "string") return error.message;
    return typeof obj.message === "string" ? obj.message : "";
  }

  function jsonRpcResult(body) {
    if (!body || typeof body !== "object") throw new Error("Empty or malformed A2A response.");
    if (body.error) throw new Error(`A2A request rejected: ${messageFromError(body) || "unknown error"}`);
    return body.result ?? body;
  }

  function taskState(task) {
    return String(task?.status?.state || "").toLowerCase();
  }

  async function waitForTerminalTask(response, signal) {
    let task = jsonRpcResult(response);
    // A2A message/send ordinarily waits for task completion. Some versions
    // return an in-progress task instead; poll using the standard tasks/get API.
    for (let i = 0; i < MAX_TASK_POLLS; i += 1) {
      if (!task || typeof task !== "object") throw new Error("Malformed A2A task.");
      const state = taskState(task);
      if (!task.id || !state || ["completed", "failed", "canceled", "rejected"].includes(state)) {
        return task;
      }
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(resolve, POLL_INTERVAL_MS);
        signal.addEventListener("abort", () => {
          clearTimeout(timeout);
          reject(new DOMException("Request aborted", "AbortError"));
        }, { once: true });
      });
      const reply = await jsonRequest(ENDPOINTS.a2a, {
        method: "POST", signal,
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", id: makeId(), method: "tasks/get", params: { id: task.id } }),
      });
      task = jsonRpcResult(reply);
    }
    throw new Error("Task did not complete within the polling window. Check the server logs.");
  }

  function findVisionArtifact(task) {
    const artifacts = Array.isArray(task?.artifacts) ? task.artifacts : [];
    const artifact = artifacts.find((entry) => entry?.name === "quipu_vision_result");
    if (!artifact) return null;
    const parts = Array.isArray(artifact.parts) ? artifact.parts : [];
    const data = parts.find((part) => part?.kind === "data" || part?.root?.kind === "data");
    return data?.data || data?.root?.data || null;
  }

  function terminalMessage(task) {
    const parts = task?.status?.message?.parts;
    if (!Array.isArray(parts)) return "";
    const text = parts.find((part) => part?.kind === "text" || part?.root?.kind === "text");
    const value = text?.text || text?.root?.text;
    if (typeof value !== "string") return "";
    try {
      const parsed = JSON.parse(value);
      return messageFromError(parsed) || String(parsed.status || "");
    } catch {
      return value.slice(0, 240);
    }
  }

  function formatAction(value) {
    const labels = {
      request_second_observation: "Request a second observation",
      request_human_review: "Request human review",
      review_conditions_or_repeat: "Review conditions or repeat",
      continue_monitoring: "Continue monitoring",
    };
    return labels[value] || String(value || "No action provided").replaceAll("_", " ");
  }

  function renderRegions(regions, total) {
    elements.regionsBody.replaceChildren();
    const list = Array.isArray(regions) ? regions : [];
    elements.regionCount.textContent = `${Number.isInteger(total) ? total : list.length} region(s)`;
    elements.regionsEmpty.hidden = list.length > 0;
    elements.regionsWrap.hidden = list.length === 0;
    list.forEach((region, index) => {
      const box = Array.isArray(region?.bbox_xywh) ? region.bbox_xywh : [];
      const row = document.createElement("tr");
      [index + 1, box[0], box[1], box[2], box[3]].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value === undefined ? "—" : String(value);
        row.appendChild(cell);
      });
      elements.regionsBody.appendChild(row);
    });
  }

  function renderEvents(events, warnings) {
    elements.events.replaceChildren();
    const entries = Array.isArray(events) ? events : [];
    const notes = Array.isArray(warnings) ? warnings : [];
    for (const entry of entries) {
      const item = document.createElement("li");
      const phase = String(entry?.phase || "Event").replaceAll("_", " ");
      const result = String(entry?.result || "").replaceAll("_", " ");
      item.textContent = `${phase}: ${result}`;
      elements.events.appendChild(item);
    }
    for (const note of notes) {
      const item = document.createElement("li");
      item.textContent = `Warning: ${String(note)}`;
      elements.events.appendChild(item);
    }
    if (elements.events.children.length === 0) {
      const item = document.createElement("li");
      item.textContent = "No events available.";
      elements.events.appendChild(item);
    }
  }

  function renderResult(artifact) {
    const output = artifact.output;
    const item = Array.isArray(output?.results) ? output.results[0] : null;
    if (!artifact.ok || !output || output.status !== "completed" || !item) {
      throw new Error(messageFromError(output) || "Vision did not produce a completed result.");
    }
    const verificationAssessed = Boolean(item.followup_processed);
    const verified = Boolean(item.verified);
    const changed = Boolean(item.change_detected);
    const fraction = Number(item.change_pixel_fraction);
    const processing = Number(output.processing_ms);

    elements.nextAction.textContent = formatAction(item.next_action || output.next_action);
    elements.summary.textContent = String(item.summary || output.summary || "No decision reason provided.");
    elements.change.textContent = changed ? "Yes" : "No";
    elements.verified.textContent = verificationAssessed ? (verified ? "Confirmed" : "Not confirmed") : "Not assessed";
    elements.fraction.textContent = Number.isFinite(fraction) ? `${(fraction * 100).toFixed(2)}%` : "—";
    elements.milliseconds.textContent = Number.isFinite(processing) ? `${processing.toFixed(2)} ms` : "—";
    renderRegions(item.regions, item.regions_total);
    renderEvents(item.events, artifact.warnings);
    elements.raw.textContent = JSON.stringify(artifact, null, 2);
    elements.error.hidden = true;
    elements.empty.hidden = true;
    elements.results.hidden = false;
    elements.analysisStatus.textContent = "Completed";
    setFeedback(verificationAssessed
      ? "Inspection complete. The follow-up was evaluated for persistent pixel change."
      : "Inspection complete. A follow-up was not processed.");
  }

  async function probeCapabilities() {
    elements.serverStatus.textContent = "Connecting to AegisForge…";
    elements.cv.textContent = "OpenCV —";
    try {
      const capabilities = await jsonRequest(ENDPOINTS.capabilities, { method: "GET" });
      ready = Boolean(capabilities.plugin_available && capabilities.a2a_task_execution && capabilities.opencv5_confirmed);
      const version = capabilities.opencv_version || "unavailable";
      elements.cv.textContent = `OpenCV ${version}`;
      elements.serverStatus.textContent = ready ? "AegisForge connected" : "Vision is unavailable";
      if (!ready) setFeedback("Enable Quipu Vision with OpenCV 5 and A2A task execution on the server.", true);
      else setFeedback("Select two images to begin.");
    } catch {
      ready = false;
      elements.serverStatus.textContent = "AegisForge disconnected";
      setFeedback("Cannot reach /vision/capabilities. Start the AegisForge server and refresh.", true);
    } finally {
      updateButton();
    }
  }

  function previewFor(config) {
    const input = $(config.input);
    const preview = $(config.preview);
    const img = $(config.image);
    const name = $(config.name);
    const oldUrl = previews.get(config.role);
    if (oldUrl) URL.revokeObjectURL(oldUrl);
    previews.delete(config.role);
    img.removeAttribute("src");
    preview.hidden = true;
    const file = input.files[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    previews.set(config.role, url);
    name.textContent = file.name;
    img.src = url;
    preview.hidden = false;
  }

  elements.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !ready) return;
    clearResult();
    busy = true;
    updateButton();
    elements.analysisStatus.textContent = "Processing";
    setFeedback("Validating images and sending an A2A request…");
    const controller = new AbortController();
    requestController = controller;
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const files = selectedImages();
      await validateDimensions(files);
      const request = await buildRequest(files);
      const response = await jsonRequest(ENDPOINTS.a2a, {
        method: "POST", signal: controller.signal,
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify(request),
      });
      const task = await waitForTerminalTask(response, controller.signal);
      const artifact = findVisionArtifact(task);
      if (!artifact) {
        throw new Error(terminalMessage(task) || `A2A task ${taskState(task) || "returned"} without a Quipu Vision result artifact.`);
      }
      renderResult(artifact);
    } catch (error) {
      const message = error?.name === "AbortError"
        ? "The request timed out. Check whether the server is still processing the task."
        : (error instanceof Error ? error.message : "Unknown inspection error.");
      showError(message);
    } finally {
      clearTimeout(timer);
      requestController = null;
      busy = false;
      updateButton();
    }
  });

  elements.form.addEventListener("reset", () => {
    if (busy) return;
    for (const config of imageFields) {
      const oldUrl = previews.get(config.role);
      if (oldUrl) URL.revokeObjectURL(oldUrl);
      previews.delete(config.role);
      $(config.image).removeAttribute("src");
      $(config.preview).hidden = true;
    }
    clearResult();
    setFeedback(ready ? "Select two images to begin." : "AegisForge is not connected.");
    // Native reset completes after this handler; update only afterward.
    setTimeout(updateButton, 0);
  });

  for (const config of imageFields) {
    $(config.input).addEventListener("change", () => {
      previewFor(config);
      if (!busy) clearResult();
      try {
        if ($(config.input).files[0]) validateFile($(config.input).files[0]);
        setFeedback(ready ? "Ready to inspect once required images are selected." : "AegisForge is not connected.");
      } catch (error) {
        setFeedback(error.message, true);
      }
      updateButton();
    });
  }

  window.addEventListener("pagehide", () => {
    requestController?.abort();
    for (const url of previews.values()) URL.revokeObjectURL(url);
  });

  clearResult();
  updateButton();
  void probeCapabilities();
})();
