// Graph of the tasks and their links (D-047), drawn with Cytoscape.js from the JSON block the server wrote.
(function () {
  "use strict";
  var source = document.getElementById("graph-data");
  if (!source || !window.cytoscape) { return; }
  var payload = JSON.parse(source.textContent);
  var css = getComputedStyle(document.documentElement);
  function v(name) { return css.getPropertyValue(name).trim(); }

  var fill = { open: v("--surface"), in_progress: v("--progress"), done: v("--ok"), dropped: v("--hover"), topic: v("--accent-soft") };
  var border = { open: v("--faint"), in_progress: v("--progress"), done: v("--ok"), dropped: v("--line"), topic: v("--accent") };
  var elements = payload.data.nodes.map(function (n) {
    return { data: { id: n.id, label: n.label.length > 60 ? n.label.slice(0, 57) + "…" : n.label, full: n.label,
                     status: n.status, kind: n.kind, url: n.url } };
  }).concat(payload.data.edges.map(function (e, i) {
    return { data: { id: "e" + i, source: e.source, target: e.target, type: e.type } };
  }));

  var cy = cytoscape({
    container: document.getElementById("graph"),
    elements: elements,
    minZoom: 0.3,
    maxZoom: 2.5,
    style: [
      { selector: "node", style: {
        "label": "data(label)", "text-wrap": "wrap", "text-max-width": "160px", "font-size": "11px",
        "font-family": getComputedStyle(document.body).fontFamily, "color": v("--text"),
        "text-valign": "center", "text-halign": "center", "shape": "round-rectangle",
        "width": function (n) { return Math.min(170, Math.max(56, (n.data("label") || "").length * 6.4)); },
        "height": function (n) { return 16 * Math.ceil((n.data("label") || "").length * 6.4 / 170) + 4; }, "padding": "9px",
        "background-color": function (n) { return fill[n.data("status")] || fill.open; },
        "border-width": 1.5, "border-color": function (n) { return border[n.data("status")] || border.open; } } },
      { selector: "node[kind = 'waiting']", style: { "border-style": "dashed" } },
      { selector: "node[status = 'done'], node[status = 'dropped']", style: { "opacity": 0.75 } },
      { selector: "node[status = 'in_progress']", style: { "color": "#1a1300" } },
      { selector: "node[status = 'topic']", style: { "font-size": "13px", "font-weight": 600, "shape": "ellipse",
        "padding": "14px", "color": v("--accent") } },
      { selector: "edge", style: { "width": 2, "line-color": v("--accent"), "curve-style": "bezier" } },
      { selector: "edge[type = 'topic']", style: { "width": 1.2, "line-color": v("--faint"), "line-style": "dashed" } },
      { selector: "edge[type = 'link']:active, edge[type = 'link']:selected", style: { "line-color": v("--danger") } },
      { selector: "node.ghost", style: { "width": 1, "height": 1, "opacity": 0, "label": "", "events": "no" } },
      { selector: "edge[type = 'ghost']", style: { "width": 2, "line-color": v("--accent"), "line-style": "dashed",
        "curve-style": "straight", "events": "no" } }
    ],
    layout: { name: "cose", animate: false, nodeDimensionsIncludeLabels: true, nodeRepulsion: function () { return 60000; },
              idealEdgeLength: function () { return 130; }, nodeOverlap: 40, componentSpacing: 120, padding: 30,
              randomize: true, numIter: 2500 }
  });

  // Zoom buttons at the edge (D-049).
  document.querySelectorAll("[data-zoom]").forEach(function (button) {
    button.addEventListener("click", function () {
      var how = button.getAttribute("data-zoom");
      if (how === "fit") { cy.animate({ fit: { padding: 30 } }, { duration: 200 }); return; }
      var level = cy.zoom() * (how === "in" ? 1.3 : 1 / 1.3);
      var box = cy.container().getBoundingClientRect();
      cy.animate({ zoom: { level: level, renderedPosition: { x: box.width / 2, y: box.height / 2 } } }, { duration: 150 });
    });
  });

  // Link mode (D-049): tap one task, a line follows the pointer, tap the second task.
  var wrap = document.querySelector(".graph-wrap");
  var modeButton = document.querySelector("[data-link-mode]");
  var status = document.querySelector("[data-graph-status]");
  var linking = false;
  var first = null;

  function say(key) {
    status.textContent = status.getAttribute("data-" + key);
    status.hidden = false;
  }

  function dropGhost() {
    cy.remove("#ghost");
    if (first) { first.style("border-width", 1.5); }
    first = null;
  }

  function setMode(on) {
    linking = on;
    modeButton.setAttribute("aria-pressed", on ? "true" : "false");
    wrap.classList.toggle("linking", on);
    dropGhost();
    if (on) { say("pick-first"); } else { status.hidden = true; }
  }

  modeButton.addEventListener("click", function () { setMode(!linking); });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape" && linking) { setMode(false); } });

  cy.on("mousemove", function (event) {
    if (first) { cy.$("#ghost").position(event.position); }
  });

  function link(a, b) {
    var data = new FormData();
    data.set("csrf", payload.csrf);
    data.set("a", a.id().slice(1));
    data.set("b", b.id().slice(1));
    fetch("/todos/links", { method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      }).then(function (result) {
        if (result.created) {
          cy.add({ data: { id: "e" + Date.now(), source: a.id(), target: b.id(), type: "link" } });
          say("linked");
        } else {
          say("already");
        }
        setTimeout(function () { if (linking && !first) { say("pick-first"); } }, 1500);
      }).catch(function () { say("failed"); });
  }

  cy.on("tap", "node", function (event) {
    var node = event.target;
    if (!linking) { window.location.href = node.data("url"); return; }
    if (node.id() === "ghost" || node.data("kind") === "topic") { return; }
    if (!first) {
      first = node;
      node.style("border-width", 4);
      // A copy: node.position() is the node's own live object, the ghost must not drag the task along.
      cy.add([{ data: { id: "ghost" }, position: { x: node.position("x"), y: node.position("y") }, classes: "ghost" },
              { data: { id: "ghost-edge", source: node.id(), target: "ghost", type: "ghost" } }]);
      say("pick-second");
      return;
    }
    var a = first;
    dropGhost();
    if (a.id() !== node.id()) { link(a, node); } else { say("pick-first"); }
  });
  cy.on("mouseover", "node", function (event) { event.target.style("border-width", 3); });
  cy.on("mouseout", "node", function (event) { event.target.style("border-width", 1.5); });
  cy.on("tap", "edge[type = 'link']", function (event) {
    if (linking) { return; }
    var edge = event.target;
    if (!window.confirm(payload.confirm + "\n" + edge.source().data("full") + "\n" + edge.target().data("full"))) { return; }
    var data = new FormData();
    data.set("csrf", payload.csrf);
    data.set("a", edge.source().id().slice(1));
    data.set("b", edge.target().id().slice(1));
    fetch("/todos/links/remove", { method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) { if (response.ok) { edge.remove(); } });
  });
})();
