// People on a task as tags (D-031): a tap adds or takes off a name and saves at once.
// Without JavaScript the tags are plain checkboxes saved with the form.
(function () {
  "use strict";
  var box = document.querySelector("[data-people-url]");
  if (!box) { return; }
  var form = box.closest("form");
  var input = form.querySelector("input[name=people_more]");

  function save() {
    var data = new FormData();
    data.set("csrf", form.querySelector("input[name=csrf]").value);
    box.querySelectorAll("input:checked").forEach(function (el) { data.append("people", el.value); });
    return fetch(box.getAttribute("data-people-url"), {
      method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.json();
    });
  }

  function find(name) {
    var wanted = name.toLowerCase();
    var found = null;
    box.querySelectorAll("input").forEach(function (el) { if (el.value.toLowerCase() === wanted) { found = el; } });
    return found;
  }

  function addTag(name) {
    var label = document.createElement("label");
    label.className = "tag";
    var checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.name = "people"; checkbox.value = name; checkbox.checked = true;
    var text = document.createElement("span");
    text.textContent = name;
    label.appendChild(checkbox); label.appendChild(text);
    box.appendChild(label);
  }

  box.addEventListener("change", function (event) {
    var checkbox = event.target;
    save().catch(function () { checkbox.checked = !checkbox.checked; });
  });

  input.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== ",") { return; }
    event.preventDefault();
    var names = input.value.split(",").map(function (s) { return s.trim().replace(/\s+/g, " "); })
      .filter(function (s) { return s; });
    if (!names.length) { return; }
    names.forEach(function (name) {
      var existing = find(name);
      if (existing) { existing.checked = true; } else { addTag(name); }
    });
    input.value = "";
    save().catch(function () { window.location.reload(); });
  });
})();
