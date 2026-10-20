// Check off a task without reloading the page (REQ-006, D-027). Without JavaScript the forms work as before.
(function () {
  "use strict";
  var toast = document.getElementById("toast");
  var hideTimer = null;

  function setCounts(counts) {
    document.querySelectorAll("[data-count]").forEach(function (el) {
      var value = counts[el.getAttribute("data-count")];
      if (value !== undefined) { el.textContent = value; }
    });
  }

  function groupOf(item) {
    var list = item.closest("ul.list");
    var head = list && list.previousElementSibling;
    return { list: list, head: head && head.classList.contains("group-title") ? head : null };
  }

  function adjustGroup(item, delta) {
    var group = groupOf(item);
    if (!group.head) { return; }
    var count = group.head.querySelector(".count");
    var value = parseInt(count.textContent, 10) + delta;
    count.textContent = value;
    group.head.hidden = value <= 0;
    group.list.hidden = value <= 0;
  }

  function post(form, status) {
    var data = new FormData(form);
    data.set("status", status);
    return fetch(form.action, {
      method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.json();
    });
  }

  function showToast(text, onUndo) {
    toast.querySelector("[data-toast-text]").textContent = text;
    var undo = toast.querySelector("button");
    undo.hidden = !onUndo;
    undo.onclick = onUndo ? function () { clearTimeout(hideTimer); toast.hidden = true; onUndo(); } : null;
    toast.hidden = false;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { toast.hidden = true; }, 6000);
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var button = event.submitter;
    if (!form.hasAttribute("data-quick") || form.dataset.plain || !button || button.name !== "status") { return; }
    event.preventDefault();
    var item = form.closest("li.task");
    var status = button.value;
    var tab = toast.getAttribute("data-tab");
    var shown = tab === "open" ? ["open", "in_progress"] : [tab];
    if (shown.indexOf(status) !== -1) {
      // Still belongs in this list (started or put back): update the row where it is.
      post(form, status).then(function (result) {
        setCounts(result.counts);
        item.setAttribute("data-status", result.status);
      }).catch(function () { form.dataset.plain = "1"; form.requestSubmit(button); });
      return;
    }
    item.classList.add("leaving");
    post(form, status).then(function (result) {
      setCounts(result.counts);
      setTimeout(function () { item.hidden = true; }, 180);
      adjustGroup(item, -1);
      showToast(toast.getAttribute("data-" + status), function () {
        post(form, result.previous).then(function (back) {
          setCounts(back.counts);
          item.hidden = false;
          item.classList.remove("leaving");
          adjustGroup(item, 1);
        }).catch(function () { window.location.reload(); });
      });
    }).catch(function () {
      // Let the normal form post show what went wrong.
      item.classList.remove("leaving");
      form.dataset.plain = "1";
      form.requestSubmit(button);
    });
  });
})();
