// People and topic of a task as tags (D-031, D-036): a tap adds or takes off a tag and saves at once.
// People take several tags, the topic one (a tap on another topic replaces it). A name picked from the
// suggestions, or typed and confirmed with Enter, comma or by leaving the field, becomes a tag at once.
// Without JavaScript the tags are plain checkboxes and radio buttons saved with the form.
(function () {
  "use strict";

  function setup(box) {
    var form = box.closest("form");
    var field = box.getAttribute("data-field");
    var single = box.hasAttribute("data-single");
    var input = form.querySelector("input[name=" + field + "_more]");

    function save() {
      var data = new FormData();
      data.set("csrf", form.querySelector("input[name=csrf]").value);
      box.querySelectorAll("input:checked").forEach(function (el) { data.append(field, el.value); });
      return fetch(box.getAttribute("data-url"), {
        method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" }
      }).then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      });
    }

    function snapshot() {
      return Array.prototype.map.call(box.querySelectorAll("input"), function (el) { return el.checked; });
    }

    function restore(state) {
      box.querySelectorAll("input").forEach(function (el, i) { el.checked = !!state[i]; });
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
      var choice = document.createElement("input");
      choice.type = single ? "radio" : "checkbox"; choice.name = field; choice.value = name;
      var text = document.createElement("span");
      text.textContent = name;
      label.appendChild(choice); label.appendChild(text);
      box.appendChild(label);
      return choice;
    }

    function pick(choice) {
      if (single) { box.querySelectorAll("input").forEach(function (el) { el.checked = el === choice; }); }
      choice.checked = true;
    }

    // A radio button cannot be unticked by a click, so the topic tags are toggled here.
    box.addEventListener("click", function (event) {
      var choice = event.target.closest("label.tag") && event.target.closest("label.tag").querySelector("input");
      if (!choice || event.target !== choice) { return; }
      var before = choice.getAttribute("data-was") === "1";
      var state = box.querySelectorAll("input");
      var previous = Array.prototype.map.call(state, function (el) { return el.getAttribute("data-was") === "1"; });
      if (single && before) { choice.checked = false; }
      state.forEach(function (el) { el.setAttribute("data-was", el.checked ? "1" : "0"); });
      save().catch(function () {
        restore(previous);
        state.forEach(function (el) { el.setAttribute("data-was", el.checked ? "1" : "0"); });
      });
    });
    box.querySelectorAll("input").forEach(function (el) { el.setAttribute("data-was", el.checked ? "1" : "0"); });

    function takeInput() {
      var names = input.value.split(single ? /\n/ : ",").map(function (s) { return s.trim().replace(/\s+/g, " "); })
        .filter(function (s) { return s; });
      if (!names.length) { return; }
      var previous = snapshot();
      names.forEach(function (name) { pick(find(name) || addTag(name)); });
      box.querySelectorAll("input").forEach(function (el) { el.setAttribute("data-was", el.checked ? "1" : "0"); });
      input.value = "";
      save().catch(function () { restore(previous); window.location.reload(); });
    }

    function suggested(value) {
      var match = false;
      document.querySelectorAll("#" + input.getAttribute("list") + " option").forEach(function (option) {
        if (option.value === value) { match = true; }
      });
      return match;
    }

    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && (single || event.key !== ",")) { return; }
      event.preventDefault();
      takeInput();
    });
    // A click on a suggestion of the list is taken at once (browsers report it without a typed character).
    input.addEventListener("input", function (event) {
      if ((!event.inputType || event.inputType === "insertReplacementText") && suggested(input.value)) { takeInput(); }
    });
    // A typed name is taken when the field is left, so "Save" is never needed for it.
    input.addEventListener("change", takeInput);
  }

  document.querySelectorAll(".tags[data-url]").forEach(setup);
})();
