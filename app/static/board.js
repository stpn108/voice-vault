// Task board (REQ-008, D-074): drag a card into another cell, move it with its buttons without reloading,
// keep the counts right, show links (also to cards that are not on screen) and remember folded rows. Without JavaScript the forms work as they are.
(function () {
  "use strict";
  var board = document.querySelector("[data-board]");
  var toast = document.getElementById("toast");
  if (!board || !toast) { return; }
  var csrf = toast.getAttribute("data-csrf");
  var svgNs = "http://www.w3.org/2000/svg";
  var hideTimer = null;
  var dragged = null;
  var shown = null;

  function send(id, status, priority) {
    var data = new FormData();
    data.set("csrf", csrf);
    data.set("status", status);
    if (priority) { data.set("priority", priority); }
    return fetch("/todos/" + id + "/status", {
      method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.json();
    });
  }

  // Tasks, not cards: with rows by person a task has one card per person (REQ-010).
  function count(scope, status) {
    var ids = {};
    scope.querySelectorAll('.bcard[data-status="' + status + '"]:not([hidden])').forEach(function (card) {
      ids[card.getAttribute("data-id")] = true;
    });
    return Object.keys(ids).length;
  }

  function copies(card) {
    return board.querySelectorAll('.bcard[data-id="' + card.getAttribute("data-id") + '"]');
  }

  function recount() {
    board.querySelectorAll("[data-col]").forEach(function (el) { el.textContent = count(board, el.getAttribute("data-col")); });
    board.querySelectorAll(".lane").forEach(function (lane) {
      lane.querySelectorAll("[data-n]").forEach(function (el) { el.textContent = count(lane, el.getAttribute("data-n")); });
    });
  }

  function setCounts(counts) {
    document.querySelectorAll("[data-count]").forEach(function (el) {
      var value = counts[el.getAttribute("data-count")];
      if (value !== undefined) { el.textContent = value; }
    });
  }

  // Put a card into the cell its status says; with a priority, into that row.
  function place(card, status, priority) {
    var cell = priority ? board.querySelector('.cell[data-status="' + status + '"][data-priority="' + priority + '"]')
      : card.closest(".lane").querySelector('.cell[data-status="' + status + '"]');
    if (priority) {
      var dot = card.querySelector(".task-title .dot");
      dot.className = "dot p" + priority;
      dot.title = cell.closest(".lane").querySelector("summary strong").textContent;
      card.setAttribute("data-priority", priority);
    }
    card.setAttribute("data-status", status);
    card.classList.toggle("finished", status === "done");
    var check = card.querySelector("button.check");
    var label = board.getAttribute(status === "done" ? "data-reopen" : "data-check");
    check.value = status === "done" ? "open" : "done";
    check.title = label;
    check.setAttribute("aria-label", label);
    if (status === "done") { cell.insertBefore(card, cell.firstChild); } else { cell.appendChild(card); }
  }

  function showToast(text, onUndo) {
    toast.querySelector("[data-toast-text]").textContent = text;
    toast.querySelector("button").onclick = function () { clearTimeout(hideTimer); toast.hidden = true; onUndo(); };
    toast.hidden = false;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { toast.hidden = true; }, 6000);
  }

  function announce(result) {
    document.dispatchEvent(new CustomEvent("todo:changed", { detail: { source: "list", result: result } }));
  }

  function settle(card, status, priority, result) {
    setCounts(result.counts);
    copies(card).forEach(function (one) {
      one.hidden = status === "dropped";
      if (status !== "dropped") { place(one, status, priority); }
    });
    recount();
    drawLinks();
    announce(result);
  }

  // Change a task and move its card. Finishing and dropping can be taken back from the toast.
  function act(card, status, priority) {
    var id = card.getAttribute("data-id");
    return send(id, status, priority).then(function (result) {
      settle(card, status, priority, result);
      if (status !== "done" && status !== "dropped") { return; }
      showToast(toast.getAttribute("data-" + status), function () {
        var before = priority ? String(result.previous_priority) : null;
        send(id, result.previous, before).then(function (back) { settle(card, result.previous, before, back); })
          .catch(function () { window.location.reload(); });
      });
    });
  }

  board.addEventListener("submit", function (event) {
    var form = event.target;
    var button = event.submitter;
    if (!form.hasAttribute("data-move") || form.dataset.plain || !button || button.name !== "status") { return; }
    event.preventDefault();
    // If the request fails, let the normal form post show what went wrong.
    act(form.closest(".bcard"), button.value, null).catch(function () { form.dataset.plain = "1"; form.requestSubmit(button); });
  });

  // Done or put back in the header list (wip.js): the card follows.
  document.addEventListener("todo:changed", function (event) {
    if (event.detail.source !== "wip") { return; }
    var result = event.detail.result;
    var card = board.querySelector('.bcard[data-id="' + result.id + '"]');
    setCounts(result.counts);
    if (!card) { return; }
    copies(card).forEach(function (one) {
      one.hidden = result.status === "dropped";
      if (!one.hidden) { place(one, result.status, null); }
    });
    recount();
    drawLinks();
  });

  // --- dragging ----------------------------------------------------------------------------------
  function dropTarget(event) {
    var el = event.target;
    return el.closest ? el.closest(".cell") || el.closest(".lane:not([open]) > summary") : null;
  }

  function clearDrop() {
    board.querySelectorAll(".drop").forEach(function (el) { el.classList.remove("drop"); });
  }

  board.addEventListener("dragstart", function (event) {
    var card = event.target.closest ? event.target.closest(".bcard") : null;
    if (!card) { return; }
    hideLinks();
    dragged = card;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.getAttribute("data-id"));
    setTimeout(function () { card.classList.add("dragging"); }, 0);
  });

  board.addEventListener("dragend", function () {
    if (dragged) { dragged.classList.remove("dragging"); }
    dragged = null;
    clearDrop();
  });

  board.addEventListener("dragover", function (event) {
    var target = dragged && dropTarget(event);
    if (!target) { return; }
    event.preventDefault();
    if (!target.classList.contains("drop")) { clearDrop(); target.classList.add("drop"); }
  });

  board.addEventListener("drop", function (event) {
    var target = dragged && dropTarget(event);
    if (!target) { return; }
    event.preventDefault();
    var card = dragged;
    // A folded row takes the card as it is and only changes the priority.
    var cell = target.classList.contains("cell") ? target : target.parentNode.querySelector('.cell[data-status="' + card.getAttribute("data-status") + '"]');
    var status = cell.getAttribute("data-status");
    var priority = cell.getAttribute("data-priority");
    if (priority === card.getAttribute("data-priority")) { priority = null; }
    if (status === card.getAttribute("data-status") && !priority) { return; }
    act(card, status, priority).catch(function () { window.location.reload(); });
  });

  // --- links (D-048, D-075): lines to the linked cards. A linked card that is not on screen (scrolled away,
  // in a folded row, in a hidden column) gets a label with its title at the nearest edge. Pointing at a card
  // shows its links; a click on its link count keeps them while scrolling, and the labels then jump to the card.
  function visibleArea() {
    var cols = board.querySelector(".board-cols").getBoundingClientRect();
    var head = document.querySelector("header.top").getBoundingClientRect();
    return { top: Math.max(cols.bottom, head.bottom, 0), bottom: window.innerHeight };
  }

  function clamp(value, low, high) { return Math.max(low, Math.min(value, high)); }

  // Where a line to a card ends: the card while it is on screen, else a label placed for it.
  function anchor(card, area, labels) {
    // A card in a folded row or in a hidden column has no place of its own: the head of that row or column stands in.
    var lane = card.closest(".lane");
    var stand = lane.matches("details:not([open])") ? lane.querySelector("summary .sum")
      : card.offsetParent ? null : board.querySelector('.col-h[for="col-' + card.getAttribute("data-status") + '"]');
    var box = (stand || card).getBoundingClientRect();
    var beside = Boolean(stand);
    if (!stand && box.bottom > area.top + 12 && box.top < area.bottom - 12) { return { box: box, card: card }; }
    var above = box.bottom <= area.top + 12, below = box.top >= area.bottom - 12;
    var label = document.createElement("button");
    label.type = "button";
    label.className = shown.pinned ? "link-label jump" : "link-label";
    label.setAttribute("data-jump", card.getAttribute("data-id"));
    label.textContent = (above ? "↑ " : below ? "↓ " : "") + card.querySelector(".task-title").textContent.trim();
    document.body.appendChild(label);
    var width = label.offsetWidth, height = label.offsetHeight;
    var x = clamp(beside ? box.right + 10 : box.left + (box.width - width) / 2, 8, window.innerWidth - width - 8);
    var y = clamp(box.top + (box.height - height) / 2, area.top + 6, area.bottom - height - 6);
    var step = below ? -(height + 4) : height + 4;
    for (var tries = 0; tries < 20 && labels.some(function (other) {  // do not cover a label placed before
      return x < other.right + 4 && x + width > other.left - 4 && y < other.bottom && y + height > other.top;
    }); tries += 1) { y += step; }
    label.style.left = x + "px";
    label.style.top = y + "px";
    box = label.getBoundingClientRect();
    labels.push(box);
    return { box: box, card: null };
  }

  function clearLinks() {
    board.querySelectorAll(".link-peer").forEach(function (card) { card.classList.remove("link-peer"); });
    document.querySelectorAll(".link-label").forEach(function (label) { document.body.removeChild(label); });
    if (shown) { shown.svg.replaceChildren(); }
  }

  function hideLinks() {
    if (!shown) { return; }
    clearLinks();
    board.classList.remove("linking", "pinned");
    shown.card.classList.remove("link-src");
    var pin = shown.card.querySelector(".link-pin");
    if (pin) { pin.setAttribute("aria-pressed", "false"); }
    document.body.removeChild(shown.svg);
    shown = null;
  }

  function drawLinks() {
    if (!shown) { return; }
    var peers = (shown.card.getAttribute("data-links") || "").split(",").filter(Boolean).map(function (id) {
      return board.querySelector('.bcard[data-id="' + id + '"]');
    }).filter(function (peer) { return peer && !peer.hidden; });
    if (shown.card.hidden || !peers.length) { hideLinks(); return; }
    clearLinks();
    var area = visibleArea(), labels = [];
    var from = anchor(shown.card, area, labels).box;
    peers.forEach(function (peer) {
      var to = anchor(peer, area, labels);
      var a = from, b = to.box;
      var y1 = a.top + a.height / 2, y2 = b.top + b.height / 2, x1, x2, c1, c2;
      if (b.left >= a.right - 4) { x1 = a.right; x2 = b.left; c1 = x1 + 40; c2 = x2 - 40; }
      else if (b.right <= a.left + 4) { x1 = a.left; x2 = b.right; c1 = x1 - 40; c2 = x2 + 40; }
      else { x1 = a.left; x2 = b.left; c1 = x1 - 34; c2 = x2 - 34; }  // same column: bow out to the left
      var path = document.createElementNS(svgNs, "path");
      path.setAttribute("d", "M" + x1 + "," + y1 + " C" + c1 + "," + y1 + " " + c2 + "," + y2 + " " + x2 + "," + y2);
      shown.svg.appendChild(path);
      if (to.card) { to.card.classList.add("link-peer"); }
    });
  }

  function showLinks(card, pinned) {
    hideLinks();
    if (!(card.getAttribute("data-links") || "")) { return; }
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", "link-layer");
    document.body.appendChild(svg);
    shown = { card: card, svg: svg, pinned: pinned };
    card.classList.add("link-src");
    board.classList.add("linking");
    board.classList.toggle("pinned", pinned);
    if (pinned) { card.querySelector(".link-pin").setAttribute("aria-pressed", "true"); }
    drawLinks();
  }

  board.addEventListener("mouseover", function (event) {
    if (dragged || (shown && shown.pinned)) { return; }
    var card = event.target.closest ? event.target.closest(".bcard") : null;
    if (shown && shown.card === card) { return; }
    hideLinks();
    if (card) { showLinks(card, false); }
  });
  board.addEventListener("mouseleave", function () { if (shown && !shown.pinned) { hideLinks(); } });

  board.addEventListener("click", function (event) {
    var pin = event.target.closest ? event.target.closest(".link-pin") : null;
    if (!pin) { return; }
    var card = pin.closest(".bcard");
    if (shown && shown.pinned && shown.card === card) { hideLinks(); } else { showLinks(card, true); }
  });

  // A label of a kept link leads to its card: unfold the row, show the column, scroll there.
  document.addEventListener("click", function (event) {
    var label = event.target.closest ? event.target.closest(".link-label") : null;
    if (!label) { return; }
    var card = board.querySelector('.bcard[data-id="' + label.getAttribute("data-jump") + '"]');
    var lane = card.closest(".lane");
    if (lane.matches("details")) { lane.open = true; }
    document.getElementById("col-" + card.getAttribute("data-status")).checked = true;
    var calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "center" });
    drawLinks();
  });

  document.addEventListener("keydown", function (event) { if (event.key === "Escape") { hideLinks(); } });

  // The lines follow while the page moves.
  var redraw = 0;
  function follow() {
    if (!shown || redraw) { return; }
    redraw = window.requestAnimationFrame(function () { redraw = 0; drawLinks(); });
  }
  window.addEventListener("scroll", follow, { passive: true });
  window.addEventListener("resize", follow);
  board.addEventListener("toggle", follow, true);
  board.addEventListener("change", follow);

  // --- folded rows are remembered in this browser ---------------------------------------------------
  board.querySelectorAll("details.lane").forEach(function (lane) {
    var key = "vv-board-lane-" + lane.getAttribute("data-lane");
    try {
      var saved = window.localStorage.getItem(key);
      if (saved !== null) { lane.open = saved === "1"; }
    } catch (error) { return; }  // storage switched off: the rows fold, they are just not remembered
    lane.addEventListener("toggle", function () {
      try { window.localStorage.setItem(key, lane.open ? "1" : "0"); } catch (error) { /* not remembered */ }
    });
  });
})();
