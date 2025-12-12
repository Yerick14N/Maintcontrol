
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
