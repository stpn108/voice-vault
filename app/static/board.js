// Task board (REQ-008, D-074): drag a card into another cell, move it with its buttons without reloading,
// keep the counts right, show links on hover and remember folded rows. Without JavaScript the forms work as they are.
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

  function count(scope, status) {
    return scope.querySelectorAll('.bcard[data-status="' + status + '"]:not([hidden])').length;
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
    card.hidden = status === "dropped";
    if (status !== "dropped") { place(card, status, priority); }
    recount();
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
    card.hidden = result.status === "dropped";
    if (!card.hidden) { place(card, result.status, null); }
    recount();
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

  // --- links on hover (D-048): lines to the linked cards on the board, the rest steps back -----------
  function hideLinks() {
    if (!shown) { return; }
    board.classList.remove("linking");
    shown.cards.forEach(function (card) { card.classList.remove("link-peer", "link-src"); });
    document.body.removeChild(shown.svg);
    shown = null;
  }

  function showLinks(card) {
    var peers = (card.getAttribute("data-links") || "").split(",").filter(Boolean).map(function (id) {
      return board.querySelector('.bcard[data-id="' + id + '"]');
    }).filter(function (peer) { return peer && peer.offsetParent; });
    if (!peers.length) { return; }
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", "link-layer");
    var a = card.getBoundingClientRect();
    peers.forEach(function (peer) {
      var b = peer.getBoundingClientRect();
      var y1 = a.top + a.height / 2, y2 = b.top + b.height / 2, x1, x2, c1, c2;
      if (b.left >= a.right - 4) { x1 = a.right; x2 = b.left; c1 = x1 + 40; c2 = x2 - 40; }
      else if (b.right <= a.left + 4) { x1 = a.left; x2 = b.right; c1 = x1 - 40; c2 = x2 + 40; }
      else { x1 = a.left; x2 = b.left; c1 = x1 - 34; c2 = x2 - 34; }  // same column: bow out to the left
      var path = document.createElementNS(svgNs, "path");
      path.setAttribute("d", "M" + x1 + "," + y1 + " C" + c1 + "," + y1 + " " + c2 + "," + y2 + " " + x2 + "," + y2);
      svg.appendChild(path);
      peer.classList.add("link-peer");
    });
    card.classList.add("link-src");
    board.classList.add("linking");
    document.body.appendChild(svg);
    shown = { card: card, svg: svg, cards: peers.concat([card]) };
  }

  board.addEventListener("mouseover", function (event) {
    if (dragged) { return; }
    var card = event.target.closest ? event.target.closest(".bcard") : null;
    if (shown && shown.card === card) { return; }
    hideLinks();
    if (card) { showLinks(card); }
  });
  board.addEventListener("mouseleave", hideLinks);
  window.addEventListener("scroll", hideLinks, { passive: true });

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
