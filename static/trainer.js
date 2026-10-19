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
const addGiftBtn = document.getElementById("add-gift-btn");
const autodetectBtn = document.getElementById("autodetect-btn");
const autodetectStatus = document.getElementById("autodetect-status");
const giftList = document.getElementById("gift-list");
const saveBtn = document.getElementById("save-btn");
const saveStatus = document.getElementById("save-status");
const ignoreTrainingToggle = document.getElementById("ignore-training-toggle");
const unreviewedOnlyToggle = document.getElementById("unreviewed-only-toggle");

const nameToId = new Map(KNOWN_GIFTS.map((g) => [g.name.trim().toLowerCase(), g.id]));

function resolveId(name) {
  return nameToId.get((name || "").trim().toLowerCase());
}

function giftIdLabel(name) {
  const id = resolveId(name);
  if (id) return { id, text: id, className: "opacity-60" };
  if (name && name.trim()) return { id: "", text: "no exact match", className: "text-error" };
  return { id: "", text: "id", className: "opacity-30" };
}

let currentImage = null;
let currentEntry = { region: null, gifts: [], ignore_for_training: false };
let drawTarget = null; // null | "region" | <gift index>
let img = null;
let scale = 1;
let dragging = false;
let dragStart = null;
let previewRect = null;
let selectedGiftIndex = null;

function imageUrl(path) {
  return "/trainer/image/" + path.split("/").map(encodeURIComponent).join("/");
}

function cloneEntry(entry) {
  const cloned = entry ? JSON.parse(JSON.stringify(entry)) : { region: null, gifts: [] };
  cloned.ignore_for_training = !!cloned.ignore_for_training;
  return cloned;
}

function sortGiftsReadingOrder(gifts) {
  const withRegion = [];
  const withoutRegion = [];
  gifts.forEach((g) => (g.subregion ? withRegion : withoutRegion).push(g));

  const items = withRegion
    .map((g) => ({ g, cy: g.subregion.y + g.subregion.h / 2, x: g.subregion.x, h: g.subregion.h }))
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

  return rows.flatMap((row) => row.items.map((item) => item.g)).concat(withoutRegion);
}

function fmtRect(r) {
  return r ? `x:${r.x} y:${r.y} w:${r.w} h:${r.h}` : "not set";
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

function loadImage(path) {
  if (!IMAGES.includes(path)) {
    imageStatus.textContent = "no such image";
    return;
  }

  currentImage = path;
  currentEntry = cloneEntry(MANUAL_DATA.gifts[path]);
  currentEntry.gifts = sortGiftsReadingOrder(currentEntry.gifts);
  drawTarget = null;
  selectedGiftIndex = null;
  previewRect = null;
  dragging = false;
  saveStatus.textContent = "";
  autodetectStatus.textContent = "";

  ignoreTrainingToggle.disabled = false;
  ignoreTrainingToggle.checked = currentEntry.ignore_for_training;

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

  renderSidebar();
  updateDrawHint();
}

function scaleRect(rect) {
  return { x: rect.x * scale, y: rect.y * scale, w: rect.w * scale, h: rect.h * scale };
}

function drawRect(rect, color, label, dashed) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  if (dashed) ctx.setLineDash([6, 4]);
  ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  if (label) {
    ctx.font = "12px sans-serif";
    const textW = ctx.measureText(label).width;
    const labelY = Math.max(0, rect.y - 16);
    ctx.fillStyle = color;
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

  if (currentEntry.region) {
    drawRect(scaleRect(currentEntry.region), "#fbbf24", "region");
  }

  currentEntry.gifts.forEach((gift, i) => {
    if (!gift.subregion || !currentEntry.region) return;
    const abs = {
      x: currentEntry.region.x + gift.subregion.x,
      y: currentEntry.region.y + gift.subregion.y,
      w: gift.subregion.w,
      h: gift.subregion.h,
    };
    const color = i === selectedGiftIndex ? "#f87171" : "#22d3ee";
    drawRect(scaleRect(abs), color, gift.name || `gift ${i + 1}`);
  });

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
  if (!img || drawTarget === null) return;
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
    commitDraw(rect);
  } else {
    render();
  }
});

function setRegion(newRegion) {
  const oldRegion = currentEntry.region;
  currentEntry.region = newRegion;
  if (!oldRegion) return;

  const dx = oldRegion.x - newRegion.x;
  const dy = oldRegion.y - newRegion.y;
  if (dx === 0 && dy === 0) return;
  currentEntry.gifts.forEach((g) => {
    if (g.subregion) {
      g.subregion = { ...g.subregion, x: g.subregion.x + dx, y: g.subregion.y + dy };
    }
  });
}

function commitDraw(rect) {
  if (drawTarget === "region") {
    setRegion(rect);
  } else if (typeof drawTarget === "number") {
    if (!currentEntry.region) {
      alert("Draw a region first.");
    } else {
      currentEntry.gifts[drawTarget].subregion = {
        x: rect.x - currentEntry.region.x,
        y: rect.y - currentEntry.region.y,
        w: rect.w,
        h: rect.h,
      };
    }
  }
  drawTarget = null;
  updateDrawHint();
  renderSidebar();
  render();
}

function updateDrawHint() {
  canvas.classList.toggle("armed", drawTarget !== null);
  drawRegionBtn.classList.toggle("btn-active", drawTarget === "region");

  if (!img) {
    drawHint.textContent = "Pick an image above to begin.";
  } else if (drawTarget === "region") {
    drawHint.textContent = "Click and drag on the image to set the region.";
  } else if (typeof drawTarget === "number") {
    const name = (currentEntry.gifts[drawTarget] && currentEntry.gifts[drawTarget].name) || `gift ${drawTarget + 1}`;
    drawHint.textContent = `Click and drag to set the subregion for "${name}".`;
  } else {
    drawHint.textContent = "";
  }
}

function updateSaveState() {
  const valid =
    !!currentImage &&
    currentEntry.gifts.every((g) => g.name && g.name.trim() && g.subregion && resolveId(g.name));
  saveBtn.disabled = !valid;
}

function renderSidebar() {
  regionDisplay.textContent = fmtRect(currentEntry.region);
  drawRegionBtn.disabled = !img;
  wholeImageBtn.disabled = !img;
  addGiftBtn.disabled = !img || !currentEntry.region;
  autodetectBtn.disabled = !img || !currentEntry.region;

  giftList.innerHTML = "";

  currentEntry.gifts.forEach((gift, i) => {
    const row = document.createElement("div");
    row.className = "bg-base-100 rounded-lg p-3 flex flex-col gap-2";
    if (i === selectedGiftIndex) row.classList.add("ring", "ring-primary");

    const invalidName = !gift.name || !gift.name.trim();
    const invalidSubregion = !gift.subregion;
    gift.id = resolveId(gift.name) || ""; // never trust a loaded/cached id -- recompute on every render
    const idInfo = giftIdLabel(gift.name);

    row.innerHTML = `
      <div class="flex items-center gap-2">
        <input type="text" class="input input-sm input-bordered flex-1 gift-name-input ${invalidName ? "input-error" : ""}"
               list="known-gifts-datalist" placeholder="gift name" value="${escapeAttr(gift.name || "")}">
        <button type="button" class="btn btn-xs btn-ghost remove-gift-btn" title="remove">✕</button>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-xs font-mono ${idInfo.className} gift-id-label" title="id is derived from name -- not editable">${idInfo.text}</span>
        <span class="text-xs ${invalidSubregion ? "text-error" : "opacity-70"} flex-1 subregion-display">${fmtRect(gift.subregion)}</span>
      </div>
      <button type="button" class="btn btn-xs btn-outline draw-subregion-btn">Draw subregion</button>
    `;

    row.querySelector(".gift-name-input").addEventListener("input", (e) => {
      gift.name = e.target.value;
      e.target.classList.toggle("input-error", !gift.name.trim());

      gift.id = resolveId(gift.name) || "";
      const info = giftIdLabel(gift.name);
      const idLabel = row.querySelector(".gift-id-label");
      idLabel.textContent = info.text;
      idLabel.className = `text-xs font-mono ${info.className} gift-id-label`;

      updateSaveState();
      render();
    });

    row.querySelector(".remove-gift-btn").addEventListener("click", () => {
      currentEntry.gifts.splice(i, 1);
      if (selectedGiftIndex === i) selectedGiftIndex = null;
      if (drawTarget === i) drawTarget = null;
      renderSidebar();
      updateDrawHint();
      render();
    });

    row.querySelector(".draw-subregion-btn").addEventListener("click", () => {
      drawTarget = i;
      selectedGiftIndex = i;
      updateDrawHint();
      renderSidebar();
      render();
    });

    giftList.appendChild(row);
  });

  updateSaveState();
}

drawRegionBtn.addEventListener("click", () => {
  drawTarget = drawTarget === "region" ? null : "region";
  updateDrawHint();
  renderSidebar();
});

wholeImageBtn.addEventListener("click", () => {
  if (!img) return;
  setRegion({ x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight });
  drawTarget = null;
  updateDrawHint();
  renderSidebar();
  render();
});

ignoreTrainingToggle.addEventListener("change", () => {
  currentEntry.ignore_for_training = ignoreTrainingToggle.checked;
});

addGiftBtn.addEventListener("click", () => {
  currentEntry.gifts.push({ id: "", name: "", subregion: null });
  selectedGiftIndex = currentEntry.gifts.length - 1;
  drawTarget = selectedGiftIndex;
  updateDrawHint();
  renderSidebar();
  render();
});

autodetectBtn.addEventListener("click", async () => {
  if (!currentImage || !currentEntry.region) return;

  if (currentEntry.gifts.length > 0) {
    const ok = confirm(`This replaces the ${currentEntry.gifts.length} gift(s) already listed with the model's guesses. Continue?`);
    if (!ok) return;
  }

  autodetectBtn.disabled = true;
  autodetectStatus.textContent = "Running detector + classifier…";
  autodetectStatus.className = "text-xs mt-1 mb-2 opacity-70";

  try {
    const resp = await fetch(AUTODETECT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: currentImage, region: currentEntry.region }),
    });

    if (!resp.ok) {
      throw new Error((await resp.text()) || resp.statusText);
    }

    const data = await resp.json();
    currentEntry.gifts = sortGiftsReadingOrder(data.gifts);
    selectedGiftIndex = null;
    drawTarget = null;

    autodetectStatus.textContent = `Found ${data.gifts.length} gift(s) (detector ${data.detector_version}, classifier ${data.classifier_version}). Review before saving.`;
    autodetectStatus.className = "text-xs mt-1 mb-2 text-success";
  } catch (err) {
    autodetectStatus.textContent = "Auto-detect failed: " + err.message;
    autodetectStatus.className = "text-xs mt-1 mb-2 text-error";
  } finally {
    updateDrawHint();
    renderSidebar();
    render();
  }
});

saveBtn.addEventListener("click", async () => {
  updateSaveState();
  if (saveBtn.disabled) {
    saveStatus.textContent = "Every gift needs a name that exactly matches a known gift, plus a subregion.";
    saveStatus.className = "text-sm mt-2 text-error";
    return;
  }

  currentEntry.gifts = sortGiftsReadingOrder(currentEntry.gifts);
  selectedGiftIndex = null;
  drawTarget = null;
  updateDrawHint();
  renderSidebar();
  render();

  saveStatus.textContent = "Saving…";
  saveStatus.className = "text-sm mt-2 opacity-70";

  try {
    const resp = await fetch(SAVE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: currentImage,
        region: currentEntry.region,
        gifts: currentEntry.gifts,
        ignore_for_training: currentEntry.ignore_for_training,
      }),
    });

    if (!resp.ok) {
      throw new Error((await resp.text()) || resp.statusText);
    }

    const data = await resp.json();
    MANUAL_DATA.gifts[currentImage] = data.entry;
    saveStatus.textContent = "Saved.";
    saveStatus.className = "text-sm mt-2 text-success";
  } catch (err) {
    saveStatus.textContent = "Save failed: " + err.message;
    saveStatus.className = "text-sm mt-2 text-error";
  }
});

function navigableImages() {
  if (!unreviewedOnlyToggle.checked) return IMAGES;
  return IMAGES.filter((path) => MANUAL_DATA.gifts[path] && MANUAL_DATA.gifts[path].unreviewed);
}

function navigateImage(dx) {
  const images = navigableImages();
  if (images.length === 0) {
    imageStatus.textContent = "no unreviewed images left";
    return;
  }
  const currentIndex = currentImage ? images.indexOf(currentImage) : (dx === 1 ? -1 : 0);
  const nextIndex = (currentIndex + dx + images.length) % images.length;

  imageSearch.value = images[nextIndex];
  loadImage(images[nextIndex]);
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
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
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
