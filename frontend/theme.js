// Light or dark theme, set before the page draws (loaded in <head>) so there is no flash.
// The opening page is always dark (<html data-theme-fixed="dark">). Elsewhere the person's choice is
// kept in this browser; without one, pages open light.
(function () {
  const root = document.documentElement;
  let choice = root.dataset.themeFixed || null;
  if (!choice) try { choice = localStorage.getItem("swm.theme"); } catch { /* storage blocked */ }
  root.dataset.theme = choice === "dark" ? "dark" : "light";
})();
