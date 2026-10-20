// Reminder of the tasks in progress (D-028): opens once a day by itself and updates without reloading.
(function () {
  "use strict";
  var panel = document.getElementById("wip");
  if (!panel || !panel.showPopover) { return; }
  var badge = document.querySelector("[data-wip-count]");
  var today = new Date().toISOString().slice(0, 10);
  try {
    if (localStorage.getItem("wip-asked") !== today) {
      localStorage.setItem("wip-asked", today);
      panel.showPopover();
    }
  } catch (e) { /* storage blocked: no automatic reminder */ }

  panel.addEventListener("submit", function (event) {
    var form = event.target;
    var button = event.submitter;
    if (!form.hasAttribute("data-wip") || !button || button.name !== "status") { return; }
    event.preventDefault();
    var data = new FormData(form);
    data.set("status", button.value);
    fetch(form.action, {
      method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      var item = form.closest("[data-wip-item]");
      item.remove();
      var left = panel.querySelectorAll("[data-wip-item]").length;
      badge.querySelector("b").textContent = left;
      if (left === 0) { panel.hidePopover(); badge.hidden = true; }
      if (window.location.pathname.indexOf("/todos") === 0) { window.location.reload(); }
    }).catch(function () { form.removeAttribute("data-wip"); form.requestSubmit(button); });
  });
})();
