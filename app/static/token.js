// Plaud token runs out soon (D-064): show the dialog unless it was dismissed in the last 8 hours.
(function () {
  "use strict";
  var dialog = document.querySelector("[data-token-dialog]");
  if (!dialog || !dialog.showModal) { return; }
  var key = dialog.getAttribute("data-key");
  var PAUSE_MS = 8 * 60 * 60 * 1000;
  var dismissed = 0;
  try { dismissed = parseInt(window.localStorage.getItem(key) || "0", 10); } catch (e) { dismissed = 0; }
  if (Date.now() - dismissed < PAUSE_MS) { return; }
  function later() {
    try { window.localStorage.setItem(key, String(Date.now())); } catch (e) { /* the dialog just comes back */ }
  }
  dialog.querySelector("[data-token-later]").addEventListener("click", function () { later(); dialog.close(); });
  dialog.addEventListener("cancel", later);  // Esc counts as "later"
  dialog.showModal();
})();
