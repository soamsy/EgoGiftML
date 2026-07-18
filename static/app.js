const searchInput = document.getElementById("search");
const affinityChecks = document.querySelectorAll(".affinity-filter");
const tierChecks = document.querySelectorAll(".tier-filter");
const cards = document.querySelectorAll(".gift-card");
const resultCount = document.getElementById("result-count");
const affinityBadge = document.getElementById("affinity-badge");
const tierBadge = document.getElementById("tier-badge");

function checkedValues(checks) {
  return Array.from(checks)
    .filter((c) => c.checked)
    .map((c) => c.value);
}

function updateBadge(badge, count) {
  badge.textContent = count;
  badge.classList.toggle("hidden", count === 0);
}

function applyFilters() {
  const query = searchInput.value.trim().toLowerCase();
  const selectedAffinities = checkedValues(affinityChecks);
  const selectedTiers = checkedValues(tierChecks);

  let visible = 0;

  cards.forEach((card) => {
    const matchesSearch = !query || card.dataset.name.includes(query);
    const matchesAffinity =
      selectedAffinities.length === 0 || selectedAffinities.includes(card.dataset.affinity);
    const matchesTier =
      selectedTiers.length === 0 || selectedTiers.includes(card.dataset.tier);

    const show = matchesSearch && matchesAffinity && matchesTier;
    card.classList.toggle("hidden", !show);
    if (show) visible += 1;
  });

  resultCount.textContent = `${visible} / ${cards.length} gifts`;
  updateBadge(affinityBadge, selectedAffinities.length);
  updateBadge(tierBadge, selectedTiers.length);
}

searchInput.addEventListener("input", applyFilters);
affinityChecks.forEach((c) => c.addEventListener("change", applyFilters));
tierChecks.forEach((c) => c.addEventListener("change", applyFilters));

applyFilters();
