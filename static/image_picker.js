const PICKER_RENDER_LIMIT = 200;

function createImagePicker({ input, dropdown, countEl, positionEl, images, giftNames, extraFilter, onSelect }) {
  let filtered = images.slice();
  let highlighted = -1;
  let current = null;

  function haystack(path) {
    return (path + " " + giftNames(path).join(" ")).toLowerCase();
  }

  function matches(path, tokens) {
    if (extraFilter && !extraFilter(path)) return false;
    if (!tokens.length) return true;
    const text = haystack(path);
    return tokens.every((t) => text.includes(t));
  }

  function recompute() {
    const tokens = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    filtered = images.filter((path) => matches(path, tokens));
    if (countEl) countEl.textContent = `${images.length} images total`;
    renderPosition();
  }

  function renderPosition() {
    if (!positionEl) return;
    const index = filtered.indexOf(current);
    positionEl.textContent = `${index >= 0 ? index + 1 : "-"}/${filtered.length}`;
  }

  function renderDropdown() {
    dropdown.innerHTML = "";
    if (!filtered.length) {
      const empty = document.createElement("div");
      empty.className = "px-3 py-2 text-sm opacity-50";
      empty.textContent = "No matches.";
      dropdown.appendChild(empty);
      return;
    }

    filtered.slice(0, PICKER_RENDER_LIMIT).forEach((path, i) => {
      const names = giftNames(path).join(" | ");
      const item = document.createElement("div");
      item.className = "picker-item px-3 py-1.5 cursor-pointer";
      if (i === highlighted) item.classList.add("bg-base-300");
      if (path === current) item.classList.add("text-primary");
      item.title = names ? `${path}\n${names}` : path;

      const file = document.createElement("div");
      file.className = "font-mono text-sm truncate";
      file.textContent = path;
      item.appendChild(file);

      const sub = document.createElement("div");
      sub.className = "text-xs truncate opacity-60";
      sub.textContent = names || "no labeled gifts";
      if (!names) sub.classList.add("italic", "opacity-30");
      item.appendChild(sub);

      item.addEventListener("mousedown", (evt) => {
        evt.preventDefault();
        select(path);
      });
      dropdown.appendChild(item);
    });

    if (filtered.length > PICKER_RENDER_LIMIT) {
      const more = document.createElement("div");
      more.className = "px-3 py-2 text-xs opacity-50";
      more.textContent = `${filtered.length - PICKER_RENDER_LIMIT} more, keep typing to narrow down`;
      dropdown.appendChild(more);
    }
  }

  function open() {
    dropdown.classList.remove("hidden");
    renderDropdown();
  }

  function close() {
    dropdown.classList.add("hidden");
    highlighted = -1;
  }

  function scrollHighlightedIntoView() {
    const el = dropdown.querySelectorAll(".picker-item")[highlighted];
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  function select(path) {
    close();
    input.blur();
    onSelect(path);
  }

  input.addEventListener("input", () => {
    recompute();
    highlighted = filtered.length ? 0 : -1;
    open();
  });

  input.addEventListener("focus", open);
  input.addEventListener("blur", close);

  input.addEventListener("keydown", (evt) => {
    const visible = Math.min(filtered.length, PICKER_RENDER_LIMIT);
    if (evt.key === "ArrowDown" || evt.key === "ArrowUp") {
      evt.preventDefault();
      if (dropdown.classList.contains("hidden")) open();
      if (!visible) return;
      const step = evt.key === "ArrowDown" ? 1 : -1;
      highlighted = (highlighted + step + visible) % visible;
      renderDropdown();
      scrollHighlightedIntoView();
    } else if (evt.key === "Enter") {
      evt.preventDefault();
      const path = filtered[highlighted >= 0 ? highlighted : 0];
      if (path) select(path);
    } else if (evt.key === "Escape") {
      close();
      input.blur();
    }
  });

  function neighbor(path, delta) {
    if (!filtered.length) return null;
    if (path === null) return delta === 1 ? filtered[0] : filtered[filtered.length - 1];
    if (delta === 1) {
      return filtered.find((p) => p > path) || filtered[0];
    }
    for (let i = filtered.length - 1; i >= 0; i--) {
      if (filtered[i] < path) return filtered[i];
    }
    return filtered[filtered.length - 1];
  }

  recompute();

  return {
    filtered: () => filtered,
    neighbor,
    setCurrent(path) {
      current = path;
      renderPosition();
    },
    refresh() {
      recompute();
      if (!dropdown.classList.contains("hidden")) renderDropdown();
    },
  };
}
