// Link two tasks by drawing a line (D-047): press the dot on the right of a task, draw to another task,
// let go. Works with mouse, pen and finger (pointer events). Esc or letting go elsewhere cancels.
(function () {
  "use strict";
  var toast = document.getElementById("link-toast");
  if (!toast) { return; }
  var csrf = toast.getAttribute("data-csrf");
  var svgNs = "http://www.w3.org/2000/svg";
  var drag = null;
  var hideTimer = null;

  function post(url, a, b) {
    var data = new FormData();
    data.set("csrf", csrf); data.set("a", a); data.set("b", b);
    return fetch(url, { method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      });
  }

  function showToast(text, onUndo) {
    toast.querySelector("[data-toast-text]").textContent = text;
    var undo = toast.querySelector("button");
    undo.hidden = !onUndo;
    undo.onclick = onUndo ? function () { toast.hidden = true; onUndo(); } : null;
    toast.hidden = false;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { toast.hidden = true; }, 6000);
  }

  function setCount(item, delta) {
    var meta = item.querySelector(".task-meta");
    var count = meta.querySelector(".link-count");
    var value = (count ? parseInt(count.getAttribute("data-n") || count.textContent, 10) : 0) + delta;
    if (!count) {
      count = document.createElement("a");
      count.className = "link-count";
      count.href = "/todos/" + item.getAttribute("data-id") + "#links";
      meta.appendChild(count);
    }
    count.setAttribute("data-n", value);
    count.textContent = "🔗 " + value;
    count.hidden = value <= 0;
  }

  function taskAt(x, y) {
    var el = document.elementFromPoint(x, y);
    var item = el && el.closest("li.task");
    return item && drag && item !== drag.item ? item : null;
  }

  function finish(cancel) {
    if (!drag) { return; }
    var target = drag.target;
    document.body.removeChild(drag.svg);
    document.documentElement.classList.remove("linking");
    if (target) { target.classList.remove("link-target"); }
    var from = drag.item;
    drag = null;
    if (cancel || !target) { return; }
    var a = from.getAttribute("data-id");
    var b = target.getAttribute("data-id");
    post("/todos/links", a, b).then(function (result) {
      if (!result.created) { showToast(toast.getAttribute("data-already"), null); return; }
      setCount(from, 1); setCount(target, 1);
      showToast(toast.getAttribute("data-linked"), function () {
        post("/todos/links/remove", a, b).then(function () { setCount(from, -1); setCount(target, -1); });
      });
    }).catch(function () { window.location.href = "/todos/" + a + "#links"; });
  }

  document.addEventListener("pointerdown", function (event) {
    var handle = event.target.closest(".link-handle");
    if (!handle || event.button > 0) { return; }
    event.preventDefault();
    var box = handle.getBoundingClientRect();
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", "link-layer");
    var line = document.createElementNS(svgNs, "line");
    var x = box.left + box.width / 2, y = box.top + box.height / 2;
    line.setAttribute("x1", x); line.setAttribute("y1", y); line.setAttribute("x2", x); line.setAttribute("y2", y);
    svg.appendChild(line);
    document.body.appendChild(svg);
    document.documentElement.classList.add("linking");
    drag = { item: handle.closest("li.task"), svg: svg, line: line, target: null };
    handle.setPointerCapture(event.pointerId);
  });

  document.addEventListener("pointermove", function (event) {
    if (!drag) { return; }
    drag.line.setAttribute("x2", event.clientX);
    drag.line.setAttribute("y2", event.clientY);
    var target = taskAt(event.clientX, event.clientY);
    if (target !== drag.target) {
      if (drag.target) { drag.target.classList.remove("link-target"); }
      if (target) { target.classList.add("link-target"); }
      drag.target = target;
    }
  });

  document.addEventListener("pointerup", function (event) {
    if (!drag) { return; }
    drag.target = taskAt(event.clientX, event.clientY);
    finish(false);
  });
  document.addEventListener("pointercancel", function () { finish(true); });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape") { finish(true); } });
})();
