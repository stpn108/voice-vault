// Living profile on the briefing page (D-057): every field saves itself when it is left.
(function () {
  "use strict";
  var box = document.querySelector("[data-profile]");
  if (!box) { return; }
  var state = box.querySelector("[data-save-state]");
  // ✕ on a topic note takes the topic off the person (D-059); the page reloads so the map follows.
  box.querySelectorAll("[data-hide-topic]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(box.getAttribute("data-hide-confirm"))) { event.preventDefault(); }
    });
  });
  box.addEventListener("change", function (event) {
    var field = event.target.getAttribute("data-field");
    if (!field) { return; }
    var data = new FormData();
    data.set("csrf", box.getAttribute("data-csrf"));
    data.set("name", box.getAttribute("data-name"));
    data.set("field", field);
    data.set("value", event.target.value);
    fetch("/person/profile", { method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        state.textContent = box.getAttribute("data-saved");
        state.classList.remove("failed");
        return response.json();
      })
      .then(function (result) {
        // The list of agreements follows what was saved (REQ-009); text goes in as text only.
        if (field !== "agreements") { return; }
        var list = box.querySelector("[data-agreements]");
        list.replaceChildren.apply(list, result.agreements.map(function (point) {
          var item = document.createElement("li");
          item.textContent = point;
          return item;
        }));
        list.hidden = result.agreements.length === 0;
        box.querySelector("[data-agreements-none]").hidden = result.agreements.length > 0;
        event.target.value = result.agreements.join("\n");
      })
      .catch(function () {
        state.textContent = box.getAttribute("data-failed");
        state.classList.add("failed");
      });
  });
})();
