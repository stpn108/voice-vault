// Reminder of the tasks in progress (D-028, D-029): the header count and the list follow every change
// without reloading. It opens only when the owner clicks it.
(function () {
  "use strict";
  var panel = document.getElementById("wip");
  var badge = document.querySelector("[data-wip-count]");
  if (!panel || !badge) { return; }
  var list = panel.querySelector("[data-wip-list]");
  // Rows added later are built from these strings; text goes in as text only.
  var texts = {
    done: panel.getAttribute("data-done"), pause: panel.getAttribute("data-pause"), note: panel.getAttribute("data-note")
  };
  var csrf = panel.getAttribute("data-csrf");

  function button(cls, value, text) {
    var b = document.createElement("button");
    b.className = cls; b.type = "submit"; b.name = "status"; b.value = value; b.textContent = text;
    return b;
  }

  function row(task) {
    var li = document.createElement("li");
    li.setAttribute("data-wip-item", "");
    var link = document.createElement("a");
    link.href = "/todos/" + task.id; link.textContent = task.title;
    var form = document.createElement("form");
    form.method = "post"; form.action = "/todos/" + task.id + "/status"; form.setAttribute("data-wip", "");
    [["csrf", csrf], ["back", "page"]].forEach(function (pair) {
      var input = document.createElement("input");
      input.type = "hidden"; input.name = pair[0]; input.value = pair[1];
      form.appendChild(input);
    });
    form.appendChild(button("primary", "done", texts.done));
    form.appendChild(button("ghost", "open", texts.pause));
    var note = document.createElement("a");
    note.className = "ghost"; note.href = "/todos/" + task.id + "#detail"; note.textContent = texts.note;
    form.appendChild(note);
    li.appendChild(link); li.appendChild(form);
    return li;
  }

  function show(result) {
    list.replaceChildren.apply(list, result.in_progress.map(row));
    var count = result.wip;
    badge.querySelector("b").textContent = count;
    badge.hidden = count === 0;
    if (count === 0 && panel.matches(":popover-open")) { panel.hidePopover(); }
  }

  // Changes made in the task list (todos.js) update the header.
  document.addEventListener("todo:changed", function (event) {
    if (event.detail.source !== "wip") { show(event.detail.result); }
  });

  panel.addEventListener("submit", function (event) {
    var form = event.target;
    var submitter = event.submitter;
    if (!form.hasAttribute("data-wip") || !submitter || submitter.name !== "status") { return; }
    event.preventDefault();
    var data = new FormData(form);
    data.set("status", submitter.value);
    fetch(form.action, {
      method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.json();
    }).then(function (result) {
      show(result);
      document.dispatchEvent(new CustomEvent("todo:changed", { detail: { source: "wip", result: result } }));
    }).catch(function () { form.removeAttribute("data-wip"); form.requestSubmit(submitter); });
  });
})();
