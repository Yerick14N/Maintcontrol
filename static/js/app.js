
async function loadTranslations(lang) {
  try {
    const res = await fetch(`/i18n/${lang}.json`);
    if (!res.ok) return;
    const dict = await res.json();
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.getAttribute("data-i18n");
      if (dict[key]) {
        el.textContent = dict[key];
      }
    });
  } catch (e) {
    console.error("i18n error", e);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const lang = document.body.getAttribute("data-lang") || "fr";
  loadTranslations(lang);
});


// UX helpers
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-assign-me]");
  if (!btn) return;
  const myId = btn.getAttribute("data-assign-me");
  const sel = document.getElementById("assignees");
  if (!sel || !myId) return;

  // select my option (keep existing selections)
  Array.from(sel.options).forEach(opt => {
    if (opt.value === myId) opt.selected = true;
  });
});
