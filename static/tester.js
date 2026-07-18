const MAX_W = 900;
const MAX_H = 700;

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const imageSearch = document.getElementById("image-search");
const imageStatus = document.getElementById("image-status");
const prevImageBtn = document.getElementById("prev-image-btn");
const nextImageBtn = document.getElementById("next-image-btn");
const drawHint = document.getElementById("draw-hint");
const drawRegionBtn = document.getElementById("draw-region-btn");
const wholeImageBtn = document.getElementById("whole-image-btn");
const regionDisplay = document.getElementById("region-display");
const runBtn = document.getElementById("run-btn");
const runStatus = document.getElementById("run-status");
const resultsBody = document.getElementById("results-body");
const timingDisplay = document.getElementById("timing-display");
const detectorSettings = document.getElementById("detector-settings");
const classifierSettings = document.getElementById("classifier-settings");
const detectorVersionSelect = document.getElementById("detector-version");
const classifierVersionSelect = document.getElementById("classifier-version");
const evalBtn = document.getElementById("eval-btn");
const evalStatus = document.getElementById("eval-status");
const evalSection = document.getElementById("eval-section");
const evalSummary = document.getElementById("eval-summary");
const evalBody = document.getElementById("eval-body");
const compareBtn = document.getElementById("compare-btn");
const compareStatus = document.getElementById("compare-status");
const compareSection = document.getElementById("compare-section");
const compareSummary = document.getElementById("compare-summary");
const compareBody = document.getElementById("compare-body");

const STATUS_COLORS = {
  matched: "#4ade80",
  misclassified: "#fb923c",
  unclassified: "#9ca3af",
  false_positive: "#f87171",
};
const STATUS_LABELS = {
  matched: "matched",
  misclassified: "wrong id",
  unclassified: "unclassified",
  false_positive: "false positive",
};

let currentImage = null;
let region = null;
let drawingRegion = false;
let img = null;
let scale = 1;
let dragging = false;
let dragStart = null;
let previewRect = null;
let lastResults = [];
let lastCompare = null; // {region, ground_truth, predictions, matches, missed_indices, score} | null

function imageUrl(path) {
  return "/tester/image/" + path.split("/").map(encodeURIComponent).join("/");
}

function fmtRect(r) {
  return r ? `x:${Math.round(r.x)} y:${Math.round(r.y)} w:${Math.round(r.w)} h:${Math.round(r.h)}` : "not set";
}

function buildEngineConfig() {
  return {
    detector: {
      version: detectorVersionSelect.value,
      conf: parseFloat(document.getElementById("detector-conf").value),
      iou: parseFloat(document.getElementById("detector-iou").value),
      imgsz: parseInt(document.getElementById("detector-imgsz").value, 10),
    },
    classifier: {
      version: classifierVersionSelect.value,
      topk: parseInt(document.getElementById("classifier-topk").value, 10),
      conf: parseFloat(document.getElementById("classifier-conf").value),
    },
  };
}

function sortResultsReadingOrder(results) {
  const items = results
    .map((r) => ({ r, cy: r.y + r.h / 2, x: r.x, h: r.h }))
    .sort((a, b) => a.cy - b.cy);

  const rows = [];
  for (const item of items) {
    const row = rows[rows.length - 1];
    if (row && Math.abs(item.cy - row.refCy) <= row.refH * 0.6) {
      row.items.push(item);
    } else {
      rows.push({ items: [item], refCy: item.cy, refH: item.h || 40 });
    }
  }
  rows.forEach((row) => row.items.sort((a, b) => a.x - b.x));

  return rows.flatMap((row) => row.items.map((item) => item.r));
}

function currentMode() {
  const checked = document.querySelector('input[name="mode"]:checked');
  return checked ? checked.dataset.mode : "both";
}

function updateModeVisibility() {
  const mode = currentMode();
  detectorSettings.classList.toggle("hidden", mode === "classifier");
  classifierSettings.classList.toggle("hidden", mode === "detector");
}

function loadImage(path) {
  if (!IMAGES.includes(path)) {
    imageStatus.textContent = "no such image";
    return;
  }

  currentImage = path;
  const knownEntry = MANUAL_DATA.gifts[path];
  region = knownEntry && knownEntry.region ? { ...knownEntry.region } : null;
  drawingRegion = false;
  previewRect = null;
  dragging = false;
  lastResults = [];
  lastCompare = null;
  runStatus.textContent = "";
  timingDisplay.textContent = "";
  compareStatus.textContent = "";
  compareSection.classList.add("hidden");
  renderResultsTable([]);

  compareBtn.disabled = !(knownEntry && knownEntry.region && (knownEntry.gifts || []).length);

  img = new Image();
  img.onload = () => {
    scale = Math.min(1, MAX_W / img.naturalWidth, MAX_H / img.naturalHeight);
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    imageStatus.textContent = `${img.naturalWidth}×${img.naturalHeight}`;
    render();
  };
  img.onerror = () => {
    imageStatus.textContent = "failed to load image";
  };
  img.src = imageUrl(path);

  wholeImageBtn.disabled = false;
  drawRegionBtn.disabled = false;
  regionDisplay.textContent = fmtRect(region);
  updateDrawHint();
  updateRunState();
}

function scaleRect(rect) {
  return { x: rect.x * scale, y: rect.y * scale, w: rect.w * scale, h: rect.h * scale };
}

function drawRect(rect, color, label, dashed, label_jitter) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  if (dashed) ctx.setLineDash([6, 4]);
  ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  if (label) {
    if (!label_jitter) label_jitter = 0;
    ctx.font = "10px sans-serif";
    const textW = ctx.measureText(label).width;
    const labelY = Math.max(0, rect.y - 16) - label_jitter;
    ctx.fillStyle = color + 'bb';
    ctx.fillRect(rect.x, labelY, textW + 8, 16);
    ctx.fillStyle = "#0a0a0a";
    ctx.fillText(label, rect.x + 4, labelY + 12);
  }
  ctx.restore();
}

function render() {
  if (!img) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

  if (region) {
    drawRect(scaleRect(region), "#fb9f24", "region");
  }

  lastResults.forEach((r, i) => {
    const label = r.gift_name
      ? `${r.gift_name} ${r.classifier_confidence !== null ? (r.classifier_confidence * 100).toFixed(0) + "%" : ""}`
      : (r.detector_confidence !== null ? `#${i + 1} ${(r.detector_confidence * 100).toFixed(0)}%` : `#${i + 1}`);
    const label_jitter = (i % 2 == 0) ? 0 : 10;
    drawRect(scaleRect({ x: r.x, y: r.y, w: r.w, h: r.h }), "#22d3ee", label.trim(), true, label_jitter);
  });

  if (lastCompare) {
    const missedSet = new Set(lastCompare.missed_indices);
    lastCompare.ground_truth.forEach((gt, i) => {
      const missed = missedSet.has(i);
      drawRect(scaleRect(gt), missed ? "#f472b6" : "#c084fc", gt.name, true);
    });
    lastCompare.matches.forEach((m) => {
      const pred = lastCompare.predictions[m.prediction_index];
      const color = STATUS_COLORS[m.status];
      const label = `${pred.gift_name || "?"} ${STATUS_LABELS[m.status]} iou:${m.iou.toFixed(2)}`;
      drawRect(scaleRect({ x: pred.x, y: pred.y, w: pred.w, h: pred.h }), color, label);
    });
  }

  if (previewRect) {
    drawRect(scaleRect(previewRect), "#a3e635", null, true);
  }
}

function canvasPos(evt) {
  const rect = canvas.getBoundingClientRect();
  const cx = (evt.clientX - rect.left) * (canvas.width / rect.width);
  const cy = (evt.clientY - rect.top) * (canvas.height / rect.height);
  return { x: cx / scale, y: cy / scale };
}

function normalizeRect(a, b) {
  return {
    x: Math.round(Math.min(a.x, b.x)),
    y: Math.round(Math.min(a.y, b.y)),
    w: Math.round(Math.abs(b.x - a.x)),
    h: Math.round(Math.abs(b.y - a.y)),
  };
}

canvas.addEventListener("mousedown", (evt) => {
  if (!img || !drawingRegion) return;
  dragging = true;
  dragStart = canvasPos(evt);
  previewRect = { x: dragStart.x, y: dragStart.y, w: 0, h: 0 };
});

canvas.addEventListener("mousemove", (evt) => {
  if (!dragging) return;
  previewRect = normalizeRect(dragStart, canvasPos(evt));
  render();
});

window.addEventListener("mouseup", () => {
  if (!dragging) return;
  dragging = false;
  const rect = previewRect;
  previewRect = null;

  if (rect && rect.w >= 3 && rect.h >= 3) {
    region = rect;
    lastResults = [];
    renderResultsTable([]);
    regionDisplay.textContent = fmtRect(region);
  }
  drawingRegion = false;
  updateDrawHint();
  updateRunState();
  render();
});

function updateDrawHint() {
  canvas.classList.toggle("armed", drawingRegion);
  drawRegionBtn.classList.toggle("btn-active", drawingRegion);

  if (!img) {
    drawHint.textContent = "Pick an image above to begin.";
  } else if (drawingRegion) {
    drawHint.textContent = "Click and drag on the image to set the region.";
  } else {
    drawHint.textContent = "";
  }
}

function updateRunState() {
  const mode = currentMode();
  const needsDetector = mode === "detector" || mode === "both";
  const needsClassifier = mode === "classifier" || mode === "both";
  const hasDetectorVersion = !needsDetector || !!detectorVersionSelect.value;
  const hasClassifierVersion = !needsClassifier || !!classifierVersionSelect.value;

  runBtn.disabled = !currentImage || !region || !hasDetectorVersion || !hasClassifierVersion;
}

drawRegionBtn.addEventListener("click", () => {
  drawingRegion = !drawingRegion;
  updateDrawHint();
});

wholeImageBtn.addEventListener("click", () => {
  if (!img) return;
  region = { x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight };
  drawingRegion = false;
  lastResults = [];
  renderResultsTable([]);
  regionDisplay.textContent = fmtRect(region);
  updateDrawHint();
  updateRunState();
  render();
});

document.querySelectorAll('input[name="mode"]').forEach((el) => {
  el.addEventListener("change", () => {
    updateModeVisibility();
    updateRunState();
  });
});

detectorVersionSelect.addEventListener("change", updateRunState);
classifierVersionSelect.addEventListener("change", updateRunState);

function renderResultsTable(results) {
  resultsBody.innerHTML = "";
  if (!results.length) {
    resultsBody.innerHTML = '<tr><td colspan="5" class="opacity-50">No detections.</td></tr>';
    return;
  }
  results.forEach((r, i) => {
    const row = document.createElement("tr");
    const detConf = r.detector_confidence !== null && r.detector_confidence !== undefined
      ? (r.detector_confidence * 100).toFixed(1) + "%" : "—";
    const clsConf = r.classifier_confidence !== null && r.classifier_confidence !== undefined
      ? (r.classifier_confidence * 100).toFixed(1) + "%" : "—";
    const giftLabel = r.gift_name ? r.gift_name : "—";
    const topkTitle = (r.topk || []).map((p) => `${p.name} ${(p.confidence * 100).toFixed(1)}%`).join("\n");
    row.innerHTML = `
      <td>${i + 1}</td>
      <td class="font-mono text-xs">${Math.round(r.x)}, ${Math.round(r.y)}, ${Math.round(r.w)}, ${Math.round(r.h)}</td>
      <td>${detConf}</td>
      <td title="${topkTitle}">${giftLabel}</td>
      <td>${clsConf}</td>
    `;
    resultsBody.appendChild(row);
  });
}

runBtn.addEventListener("click", async () => {
  if (!currentImage || !region) return;

  const mode = currentMode();
  runBtn.disabled = true;
  runStatus.textContent = "Running…";
  runStatus.className = "text-sm opacity-70";
  lastCompare = null;
  compareSection.classList.add("hidden");
  compareStatus.textContent = "";

  const payload = { image: currentImage, region, mode, ...buildEngineConfig() };

  try {
    const resp = await fetch(RUN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      throw new Error((await resp.text()) || resp.statusText);
    }
    const data = await resp.json();
    lastResults = sortResultsReadingOrder(data.results);
    renderResultsTable(lastResults);
    render();

    const timingParts = Object.entries(data.timing_ms || {}).map(([k, v]) => `${k}: ${v}ms`);
    timingDisplay.textContent = timingParts.join("  ·  ");
    runStatus.textContent = `Found ${lastResults.length} result(s).`;
    runStatus.className = "text-sm text-success";
  } catch (err) {
    runStatus.textContent = "Run failed: " + err.message;
    runStatus.className = "text-sm text-error";
  } finally {
    updateRunState();
  }
});

function fmtPct(n, d) {
  return d ? `${((n / d) * 100).toFixed(0)}%` : "—";
}

function renderCompareTable(data) {
  compareBody.innerHTML = "";
  const rows = [];

  data.matches.forEach((m) => {
    const pred = data.predictions[m.prediction_index];
    const gt = m.ground_truth_index !== null ? data.ground_truth[m.ground_truth_index] : null;
    rows.push({
      gtLabel: gt ? gt.name : "—",
      predLabel: pred.gift_name || "—",
      status: m.status,
      iou: m.iou,
      detConf: pred.detector_confidence,
      clsConf: pred.classifier_confidence !== null ? pred.classifier_confidence : (pred.topk[0] ? pred.topk[0].confidence : null),
    });
  });
  data.missed_indices.forEach((idx) => {
    const gt = data.ground_truth[idx];
    rows.push({ gtLabel: gt.name, predLabel: "—", status: "missed", iou: 0, detConf: null, clsConf: null });
  });

  rows.sort((a, b) => (a.status === "matched") - (b.status === "matched"));

  rows.forEach((r, i) => {
    const row = document.createElement("tr");
    const color = r.status === "missed" ? "#f472b6" : STATUS_COLORS[r.status];
    const label = r.status === "missed" ? "missed" : STATUS_LABELS[r.status];
    row.innerHTML = `
      <td>${i + 1}</td>
      <td>${r.gtLabel}</td>
      <td>${r.predLabel}</td>
      <td><span style="color:${color}">${label}</span></td>
      <td>${r.status === "missed" ? "—" : r.iou.toFixed(2)}</td>
      <td>${r.detConf !== null ? (r.detConf * 100).toFixed(0) + "%" : "—"}</td>
      <td>${r.clsConf !== null ? (r.clsConf * 100).toFixed(0) + "%" : "—"}</td>
    `;
    compareBody.appendChild(row);
  });
}

compareBtn.addEventListener("click", async () => {
  if (!currentImage) return;

  compareBtn.disabled = true;
  compareStatus.textContent = "Comparing to training data…";
  compareStatus.className = "text-sm opacity-70";
  lastResults = [];
  renderResultsTable([]);
  timingDisplay.textContent = "";

  try {
    const resp = await fetch(COMPARE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: currentImage, ...buildEngineConfig() }),
    });
    if (!resp.ok) {
      throw new Error((await resp.text()) || resp.statusText);
    }
    const data = await resp.json();
    lastCompare = data;
    region = { ...data.region }; // ground truth is only meaningful relative to its own region
    regionDisplay.textContent = fmtRect(region);

    compareSection.classList.remove("hidden");
    renderCompareTable(data);
    render();

    const s = data.score;
    compareSummary.textContent =
      `expected ${s.expected}  ·  matched ${s.matched}  ·  wrong id ${s.misclassified}  ·  ` +
      `unclassified ${s.unclassified}  ·  false pos. ${s.false_positives}  ·  missed ${s.missed}`;
    compareStatus.textContent = "Done.";
    compareStatus.className = "text-sm text-success";
  } catch (err) {
    compareStatus.textContent = "Compare failed: " + err.message;
    compareStatus.className = "text-sm text-error";
  } finally {
    compareBtn.disabled = false;
  }
});

function renderEvalTable(rows) {
  evalBody.innerHTML = "";
  rows.forEach((r) => {
    const row = document.createElement("tr");
    if (r.missed || r.misclassified || r.false_positives) row.classList.add("bg-error/10");
    else if (r.unclassified) row.classList.add("bg-warning/10");

    row.innerHTML = `
      <td><button type="button" class="btn btn-2xs btn-outline load-eval-btn">Load</button></td>
      <td class="font-mono text-xs">${r.image}${r.ignore_for_training ? ' <span class="badge badge-xs badge-ghost" title="ignore_for_training">ignored in training</span>' : ""}</td>
      <td>${r.ignore_for_training ? "yes" : ""}</td>
      <td>${r.expected}</td>
      <td>${r.matched} <span class="opacity-50">(${fmtPct(r.matched, r.expected)})</span></td>
      <td>${r.misclassified || ""}</td>
      <td>${r.unclassified || ""}</td>
      <td>${r.false_positives || ""}</td>
      <td>${r.missed || ""}</td>
      <td>${r.avg_detector_conf !== null ? (r.avg_detector_conf * 100).toFixed(0) + "%" : "—"}</td>
      <td>${r.avg_classifier_conf !== null ? (r.avg_classifier_conf * 100).toFixed(0) + "%" : "—"}</td>
    `;
    row.querySelector(".load-eval-btn").addEventListener("click", () => {
      imageSearch.value = r.image;
      loadImage(r.image);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    evalBody.appendChild(row);
  });
}

evalBtn.addEventListener("click", async () => {
  evalBtn.disabled = true;
  evalStatus.textContent = "Evaluating every labeled image in manual_train.json…";
  evalStatus.className = "text-sm opacity-70";

  try {
    const resp = await fetch(EVALUATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildEngineConfig()),
    });
    if (!resp.ok) {
      throw new Error((await resp.text()) || resp.statusText);
    }
    const data = await resp.json();

    evalSection.classList.remove("hidden");
    renderEvalTable(data.rows);

    const s = data.summary;
    evalSummary.textContent =
      `detector ${data.detector_version}, classifier ${data.classifier_version}  ·  ` +
      `${s.images} images (${s.skipped} skipped)  ·  ` +
      `matched ${s.matched}/${s.expected} (${fmtPct(s.matched, s.expected)})  ·  ` +
      `wrong id ${s.misclassified}  ·  unclassified ${s.unclassified}  ·  ` +
      `false pos. ${s.false_positives}  ·  missed ${s.missed}`;

    evalStatus.textContent = `Done. ${data.rows.length} images scored, worst first.`;
    evalStatus.className = "text-sm text-success";
  } catch (err) {
    evalStatus.textContent = "Evaluation failed: " + err.message;
    evalStatus.className = "text-sm text-error";
  } finally {
    evalBtn.disabled = false;
  }
});

function navigateImage(delta) {
  if (IMAGES.length === 0) return;
  const currentIndex = currentImage ? IMAGES.indexOf(currentImage) : (delta === 1 ? -1 : 0);
  const nextIndex = (currentIndex + delta + IMAGES.length) % IMAGES.length;

  imageSearch.value = IMAGES[nextIndex];
  loadImage(IMAGES[nextIndex]);
}

imageSearch.addEventListener("change", () => {
  const value = imageSearch.value.trim();
  if (IMAGES.includes(value)) loadImage(value);
});

imageSearch.addEventListener("keydown", (evt) => {
  if (evt.key === "Enter") {
    const value = imageSearch.value.trim();
    if (IMAGES.includes(value)) loadImage(value);
  }
});

function isFormField(el) {
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

document.addEventListener("keydown", (evt) => {
  if (evt.key !== "PageDown" && evt.key !== "PageUp") return;
  if (isFormField(document.activeElement)) return;

  evt.preventDefault();
  navigateImage(evt.key === "PageDown" ? 1 : -1);
});

prevImageBtn.addEventListener("click", () => navigateImage(-1));
nextImageBtn.addEventListener("click", () => navigateImage(1));

prevImageBtn.disabled = IMAGES.length === 0;
nextImageBtn.disabled = IMAGES.length === 0;

updateModeVisibility();
updateRunState();
