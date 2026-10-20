// Task page (D-032): every field saves itself in the background when it is left; there is no Save button.
// Without JavaScript the form keeps its Save button (inside <noscript>).
(function () {
  "use strict";
  var form = document.querySelector("form[data-autosave]");
  if (!form) { return; }
  var state = form.querySelector("[data-save-state]");
  var pill = form.querySelector("[data-status-pill]");
  var pillClass = { done: "state-verified", in_progress: "state-progress", open: "state-waiting" };
  var queue = Promise.resolve();
  var view = form.querySelector("[data-detail-view]");
  var detail = form.querySelector("textarea[name=detail]");

  // The details show as formatted text; a click turns them into the text field, leaving it shows them again.
  function showView() { view.hidden = false; detail.hidden = true; }
  function edit() { view.hidden = true; detail.hidden = false; detail.focus(); }
  showView();
  view.addEventListener("click", edit);
  view.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); edit(); } });
  detail.addEventListener("blur", function () { setTimeout(showView, 0); });

  function mark(field, cls) {
    var box = field.closest(".field");
    if (!box) { return; }
    box.classList.remove("saved", "failed");
    box.classList.add(cls);
    if (cls === "saved") { setTimeout(function () { box.classList.remove("saved"); }, 1200); }
  }

  function show(key) {
    state.textContent = state.getAttribute("data-" + key);
    state.classList.toggle("failed", key === "failed");
  }

  function save(field) {
    if (!form.reportValidity()) { mark(field, "failed"); show("failed"); return; }
    var data = new FormData(form);
    data.delete("people_more");  // names typed there are handled by people.js
    show("saving");
    // One save at a time, in the order the fields were left.
    queue = queue.then(function () {
      return fetch(form.action, {
        method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" }
      }).then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      }).then(function (result) {
        mark(field, "saved");
        show("saved");
        view.innerHTML = result.detail_html;  // rendered and escaped by the server (markdown_lite)
        if (field.name === "status") {
          var select = form.querySelector("select[name=status]");
          pill.textContent = select.options[select.selectedIndex].text;
          pill.className = "pill " + (pillClass[result.status] || "");
        }
        document.dispatchEvent(new CustomEvent("todo:changed", { detail: { source: "page", result: result } }));
      }).catch(function () { mark(field, "failed"); show("failed"); });
    });
  }

  form.addEventListener("change", function (event) {
    var field = event.target;
    if (!field.name || field.name === "people" || field.name === "people_more") { return; }
    save(field);
  });
  // Enter in a one-line field leaves it instead of posting the whole page.
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (document.activeElement && document.activeElement.blur) { document.activeElement.blur(); }
  });
})();
