// Living profile on the briefing page (D-057): every field saves itself when it is left.
(function () {
  "use strict";
  var box = document.querySelector("[data-profile]");
  if (!box) { return; }
  var state = box.querySelector("[data-save-state]");
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
      })
      .catch(function () {
        state.textContent = box.getAttribute("data-failed");
        state.classList.add("failed");
      });
  });
})();
