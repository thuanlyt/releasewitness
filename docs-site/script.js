const docsIndex = [
  { title: "Get started", description: "Keep Claude Code, Codex or another runtime native; add relwit qa and relwit gate for source-bound assurance.", href: "/getting-started" },
  { title: "Assurance gate", description: "Run configured QA, fingerprint the release source and fail when passing evidence becomes stale.", href: "/operations" },
  { title: "Architecture", description: "Runtime executes; ReleaseWitness verifies source-bound QA and release state.", href: "/architecture" },
  { title: "Redundancy study", description: "Inspect the 56-session empirical study that deprecated full RelWit supervision.", href: "/case-study#redundancy-study" },
  { title: "OSBlog dogfood case study", description: "See the earlier real workload that motivated evidence provenance, recovery and source-bound QA.", href: "/case-study" },
  { title: "Hướng dẫn tiếng Việt", description: "Dùng RelWit như QA/release gate, giữ orchestration trong coding runtime native.", href: "/vi" },
]

const menuButton = document.querySelector(".menu-toggle");
const primaryNav = document.querySelector(".primary-nav");
if (menuButton && primaryNav) {
  menuButton.addEventListener("click", () => {
    const open = menuButton.getAttribute("aria-expanded") === "true";
    menuButton.setAttribute("aria-expanded", String(!open));
    primaryNav.classList.toggle("open", !open);
  });
}

const searchForm = document.querySelector(".search-form");
const searchInput = document.querySelector("#doc-search");
const searchResults = document.querySelector("#search-results");
function renderResults(query) {
  if (!searchResults || !searchInput) return;
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    searchResults.hidden = true;
    searchInput.setAttribute("aria-expanded", "false");
    searchResults.replaceChildren();
    return;
  }
  const matches = docsIndex.filter((item) => `${item.title} ${item.description}`.toLowerCase().includes(normalized));
  searchResults.replaceChildren();
  if (matches.length === 0) {
    const empty = document.createElement("p");
    empty.className = "search-empty";
    empty.textContent = "No exact match yet. Try “worker”, “runtime” or “production gate”.";
    searchResults.append(empty);
  } else {
    matches.forEach((item) => {
      const link = document.createElement("a");
      link.className = "search-result";
      link.href = item.href;
      const title = document.createElement("strong");
      title.textContent = item.title;
      const description = document.createElement("small");
      description.textContent = item.description;
      link.append(title, description);
      searchResults.append(link);
    });
  }
  searchResults.hidden = false;
  searchInput.setAttribute("aria-expanded", "true");
}
if (searchInput) searchInput.addEventListener("input", () => renderResults(searchInput.value));
if (searchForm) {
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = searchInput ? searchInput.value.trim().toLowerCase() : "";
    const match = docsIndex.find((item) => `${item.title} ${item.description}`.toLowerCase().includes(query));
    if (match && query) window.location.href = match.href;
    else renderResults(query);
  });
}
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && searchInput) {
    event.preventDefault();
    searchInput.focus();
  }
});
