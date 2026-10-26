// Graph of the tasks and their links (D-047), drawn with Cytoscape.js from the JSON block the server wrote.
(function () {
  "use strict";
  var source = document.getElementById("graph-data");
  if (!source || !window.cytoscape) { return; }
  var fcose = Boolean(window.cytoscapeFcose);
  var withTopics = JSON.parse(source.textContent).data.nodes.some(function (n) { return n.kind === "topic"; });
  if (fcose) { cytoscape.use(window.cytoscapeFcose); }
  var payload = JSON.parse(source.textContent);
  // A person's own Mindmap (D-058): read-only, the person fixed in the middle, nothing is saved.
  var personal = Boolean(payload.data.person);
  var css = getComputedStyle(document.documentElement);
  function v(name) { return css.getPropertyValue(name).trim(); }

  var font = getComputedStyle(document.body).fontFamily;
  // Status fills the node; an urgent or high priority shows as a coloured rim (D-050).
  var fill = { open: v("--surface"), in_progress: v("--progress"), done: v("--ok"), dropped: v("--hover"), topic: v("--accent") };
  var rim = { 1: v("--danger"), 2: v("--warn") };
  var elements = payload.data.nodes.map(function (n) {
    // Spread out from the start (D-053): nodes stacked at one point make Cytoscape mark their lines as
    // undrawable, and it keeps that until a node moves.
    // A box the owner placed or resized keeps that (D-054).
    if (personal && n.kind === "person") { n.x = 0; n.y = 0; }
    var data = { id: n.id, short: n.label.length > 70 ? n.label.slice(0, 67) + "…" : n.label, full: n.label,
                 status: n.status, kind: n.kind, url: n.url, priority: n.priority || 3, saved: n.x !== undefined };
    if (n.w) { data.w = n.w; data.h = n.h; }
    return { data: data, position: n.x !== undefined ? { x: n.x, y: n.y } : { x: Math.random() * 1500, y: Math.random() * 1000 } };
  }).concat(payload.data.edges.map(function (e, i) {
    return { data: { id: "e" + i, source: e.source, target: e.target, type: e.type, score: e.score || 0, why: e.why || null } };
  }));

  // Text and size: a box the owner made bigger shows the whole title and wraps at its own width.
  function labelOf(n) { return n.data("w") ? n.data("full") : (n.data("short") || ""); }
  function textWidth(n) { return n.data("w") || Math.min(210, Math.max(70, labelOf(n).length * 7.2)); }
  function textLines(n) { return Math.max(1, Math.ceil(labelOf(n).length * 7.2 / Math.max(60, textWidth(n) - 6))); }
  function textHeight(n) { return n.data("h") || 17 * textLines(n); }

  var cy = cytoscape({
    container: document.getElementById("graph"),
    elements: elements,
    minZoom: 0.25,
    maxZoom: 3,
    style: [
      { selector: "node", style: {
        "label": labelOf, "text-wrap": "wrap", "text-max-width": function (n) { return textWidth(n) + "px"; }, "font-size": "13px",
        "font-family": font, "font-weight": 500, "color": v("--text"), "line-height": 1.3,
        "text-valign": "center", "text-halign": "center", "shape": "round-rectangle",
        "width": textWidth, "height": textHeight, "padding": "12px",
        "background-color": function (n) { return fill[n.data("status")] || fill.open; },
        "border-width": function (n) { return rim[n.data("priority")] && n.data("status") !== "done" ? 2.5 : 1.2; },
        "border-color": function (n) { return rim[n.data("priority")] || v("--line"); },
        "transition-property": "opacity", "transition-duration": "120ms" } },
      { selector: "node[kind = 'waiting']", style: { "border-style": "dashed", "border-width": 1.8, "border-color": v("--muted") } },
      { selector: "node[status = 'done'], node[status = 'dropped']", style: { "opacity": 0.6, "color": v("--muted") } },
      { selector: "node[status = 'in_progress']", style: { "color": "#1a1300", "font-weight": 600 } },
      { selector: "node[status = 'topic']", style: { "font-size": "15px", "font-weight": 700, "color": v("--surface"),
        "shape": "round-rectangle", "padding": "16px", "border-width": 0,
        "width": function (n) { return n.data("w") || Math.max(80, labelOf(n).length * 8.6); },
        "height": function (n) { return n.data("h") || 20; } } },
      { selector: "node[kind = 'person']", style: { "font-size": "17px", "font-weight": 700, "color": v("--surface"),
        "background-color": v("--text"), "shape": "ellipse", "padding": "22px", "border-width": 0,
        "width": function (n) { return Math.max(90, labelOf(n).length * 10); }, "height": 30 } },
      // "bezier" draws a single edge straight and always finds its end points; "unbundled-bezier" failed at some
      // angles and left lines out (D-053).
      { selector: "edge", style: { "width": 2.5, "line-color": v("--accent"), "curve-style": "bezier", "opacity": 0.8,
        "transition-property": "opacity", "transition-duration": "120ms" } },
      { selector: "edge[type = 'topic']", style: { "width": 1.5, "line-color": v("--faint"), "line-style": "dotted",
        "curve-style": "straight", "opacity": 0.9 } },
      { selector: "edge[type = 'person']", style: { "width": 2, "line-color": v("--muted"), "curve-style": "straight", "opacity": 0.7 } },
      // A probable connection: dashed, the stronger the evidence the thicker (D-058).
      { selector: "edge[type = 'inferred']", style: { "line-style": "dashed", "line-color": v("--series-2") || v("--warn"),
        "width": function (e) { return Math.min(7, 1 + e.data("score")); }, "curve-style": "bezier", "opacity": 0.75 } },
      { selector: "edge[type = 'link']:active, edge[type = 'link']:selected", style: { "line-color": v("--danger"), "opacity": 1 } },
      { selector: ".faded", style: { "opacity": 0.12 } },
      { selector: ".deg2", style: { "opacity": 0.75 } },
      { selector: ".deg3", style: { "opacity": 0.5 } },
      { selector: "node.focus", style: { "border-width": 3, "border-color": v("--accent") } },
      { selector: "node.ghost", style: { "width": 1, "height": 1, "opacity": 0, "label": "", "events": "no" } },
      { selector: "edge[type = 'ghost']", style: { "width": 2, "line-color": v("--accent"), "line-style": "dashed",
        "curve-style": "straight", "events": "no" } }
    ],
    layout: { name: "preset" }
  });

  // fCoSE (D-050): compact, without overlaps; separate groups are packed side by side.
  function layoutOptions(fixed) {
    if (!fcose) { return { name: "cose", animate: false, nodeDimensionsIncludeLabels: true, padding: 40 }; }
    var options = { name: "fcose", quality: "proof", animate: false, randomize: true, nodeDimensionsIncludeLabels: true,
                    packComponents: true, tile: true, tilingPaddingVertical: 30, tilingPaddingHorizontal: 30,
                    nodeSeparation: withTopics ? 80 : 60, nodeRepulsion: function () { return withTopics ? 9000 : 6500; },
                    idealEdgeLength: function (e) { return e.data("type") === "topic" ? 55 : 90; },
                    edgeElasticity: function (e) { return e.data("type") === "topic" ? 0.6 : 0.35; },
                    gravity: 0.3, gravityRange: 2.5, numIter: 5000, padding: 30 };
    if (fixed && fixed.length) { options.fixedNodeConstraint = fixed; }
    if (personal) { options.idealEdgeLength = function (e) { return e.data("type") === "inferred" ? 160 : 110; }; }
    return options;
  }

  // Remembered boxes stay where they are (D-054); only boxes without a place are laid out around them.
  function nudge() {
    // Every node "moves" once more, so Cytoscape works out every line again for the final positions (D-053).
    cy.nodes().forEach(function (n) { n.position({ x: n.position("x"), y: n.position("y") + 0.01 }); });
  }
  var placed = cy.nodes().filter(function (n) { return n.data("saved"); });
  var fresh = cy.nodes().filter(function (n) { return !n.data("saved"); });
  if (fresh.length) {
    cy.layout(layoutOptions(placed.map(function (n) {
      return { nodeId: n.id(), position: { x: n.position("x"), y: n.position("y") } };
    }))).run();
  }
  nudge();

  // --- saving positions and sizes (D-054) ---
  var pending = {};
  var saveTimer = null;
  function remember(nodes, withSize) {
    nodes.forEach(function (n) {
      if (n.id() === "ghost") { return; }
      var entry = { key: n.id(), x: Math.round(n.position("x") * 10) / 10, y: Math.round(n.position("y") * 10) / 10 };
      if (withSize || (pending[n.id()] && "w" in pending[n.id()])) {
        entry.w = n.data("w") || null; entry.h = n.data("h") || null;
      }
      pending[n.id()] = entry;
      n.data("saved", true);
    });
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 400);
  }
  function flush() {
    var entries = Object.keys(pending).map(function (key) { return pending[key]; });
    pending = {};
    if (!entries.length) { return; }
    var data = new FormData();
    data.set("csrf", payload.csrf);
    data.set("layout", JSON.stringify(entries));
    fetch("/todos/graph/layout", { method: "POST", body: data, credentials: "same-origin", headers: { "Accept": "application/json" } });
  }
  if (fresh.length && !personal) { remember(fresh, false); }
  if (!personal) { cy.on("dragfree", "node", function (event) { remember(event.target, false); }); }

  // Readable from the start: fit, but never so small that the text cannot be read (D-050).
  function view() {
    cy.fit(undefined, 30);
    // Never start so small that the text cannot be read; ⤢ and full screen show everything.
    if (cy.zoom() < 0.75) { cy.zoom(0.75); cy.center(); }
    if (cy.zoom() > 1.5) { cy.zoom(1.5); cy.center(); }
  }
  view();

  // Pointing at a task highlights everything reachable through links, at any distance (D-052): direct
  // neighbours fully, the 2nd degree a little lighter, the 3rd and further lighter still; the rest fades.
  // Topics are not bridges (through a topic everything would be connected), but the task's topics stay.
  var DEGREES = ["deg2", "deg3"];

  function clearFocus() { cy.elements().removeClass("faded deg2 deg3"); }

  function focus(start) {
    if (start.data("kind") === "topic") {
      cy.elements().not(start.closedNeighborhood()).addClass("faded");
      return;
    }
    var distance = {};
    distance[start.id()] = 0;
    var queue = [start];
    while (queue.length) {
      var node = queue.shift();
      node.connectedEdges("[type = 'link']").connectedNodes().forEach(function (next) {
        if (distance[next.id()] === undefined) {
          distance[next.id()] = distance[node.id()] + 1;
          queue.push(next);
        }
      });
    }
    cy.batch(function () {
      cy.nodes().forEach(function (n) {
        var d = distance[n.id()];
        if (d === undefined) {
          var ownTopic = n.data("kind") === "topic" && n.edgesWith(start).length > 0;
          if (!ownTopic) { n.addClass("faded"); }
        } else if (d >= 2) {
          n.addClass(DEGREES[Math.min(d, 3) - 2]);
        }
      });
      cy.edges().forEach(function (e) {
        var a = distance[e.source().id()], b = distance[e.target().id()];
        if (e.data("type") === "link" && a !== undefined && b !== undefined) {
          var d = Math.max(a, b);
          if (d >= 2) { e.addClass(DEGREES[Math.min(d, 3) - 2]); }
        } else if (!(e.data("type") === "topic" && (e.source().same(start) || e.target().same(start)))) {
          e.addClass("faded");
        }
      });
    });
  }

  cy.on("mouseover", "node", function (event) {
    if (event.target.id() === "ghost") { return; }
    clearFocus();
    focus(event.target);
  });
  cy.on("mouseout", "node", clearFocus);

  // Full screen (D-050): the browser's own when it has one, else the box fills the window.
  var wrapBox = document.querySelector(".graph-wrap");
  var fullButton = document.querySelector("[data-fullscreen]");
  function toggleFull() {
    if (wrapBox.requestFullscreen && !document.fullscreenElement) {
      wrapBox.requestFullscreen().catch(function () { wrapBox.classList.toggle("max"); afterResize(); });
    } else if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      wrapBox.classList.toggle("max");
      afterResize();
    }
  }
  function afterResize() { setTimeout(function () { cy.resize(); view(); }, 60); }
  fullButton.addEventListener("click", toggleFull);
  document.addEventListener("fullscreenchange", function () {
    fullButton.setAttribute("aria-pressed", document.fullscreenElement ? "true" : "false");
    afterResize();
  });

  // Pointing at a probable connection says why it is drawn (D-058).
  var why = document.querySelector("[data-graph-status]");
  cy.on("mouseover", "edge[type = 'inferred']", function (event) {
    var counts = event.target.data("why") || {};
    why.textContent = why.getAttribute("data-why").replace("{talks}", counts.talks || 0)
      .replace("{links}", counts.links || 0).replace("{people}", counts.people || 0);
    why.hidden = false;
  });
  cy.on("mouseout", "edge[type = 'inferred']", function () { why.hidden = true; });
  // Zoom buttons at the edge (D-049).
  document.querySelectorAll("[data-zoom]").forEach(function (button) {
    button.addEventListener("click", function () {
      var how = button.getAttribute("data-zoom");
      if (how === "fit") { cy.animate({ fit: { padding: 40 } }, { duration: 200 }); return; }
      var level = cy.zoom() * (how === "in" ? 1.3 : 1 / 1.3);
      var box = cy.container().getBoundingClientRect();
      cy.animate({ zoom: { level: level, renderedPosition: { x: box.width / 2, y: box.height / 2 } } }, { duration: 150 });
    });
  });

  if (personal) {
    cy.on("tap", "node", function (event) { window.location.href = event.target.data("url"); });
    return;
  }

  // --- resizing a box (D-054): a corner handle appears on hover; dragging it sets the box's size ---
  var handle = document.querySelector("[data-node-resize]");
  var handleNode = null;
  var resizing = null;
  var hideHandleTimer = null;

  function placeHandle() {
    if (!handleNode || handleNode.removed()) { handle.hidden = true; return; }
    var box = handleNode.renderedBoundingBox({ includeLabels: false });
    handle.style.left = (box.x2 - 13) + "px";
    handle.style.top = (box.y2 - 13) + "px";
    handle.hidden = false;
  }
  cy.on("mouseover", "node", function (event) {
    if (linking || resizing || event.target.id() === "ghost") { return; }
    clearTimeout(hideHandleTimer);
    handleNode = event.target;
    placeHandle();
  });
  cy.on("mouseout", "node", function () {
    if (resizing) { return; }
    hideHandleTimer = setTimeout(function () { handle.hidden = true; handleNode = null; }, 600);
  });
  cy.on("pan zoom position", function () { if (handleNode) { placeHandle(); } });
  handle.addEventListener("pointerenter", function () { clearTimeout(hideHandleTimer); });
  handle.addEventListener("pointerdown", function (event) {
    if (!handleNode) { return; }
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    resizing = { node: handleNode, x: event.clientX, y: event.clientY, w: handleNode.width(), h: handleNode.height() };
  });
  handle.addEventListener("pointermove", function (event) {
    if (!resizing) { return; }
    var zoom = cy.zoom();
    var w = Math.max(70, Math.min(1100, resizing.w + 2 * (event.clientX - resizing.x) / zoom));
    var h = Math.max(20, Math.min(1100, resizing.h + 2 * (event.clientY - resizing.y) / zoom));
    resizing.node.data({ w: Math.round(w), h: Math.round(h) });
    placeHandle();
  });
  handle.addEventListener("pointerup", function () {
    if (!resizing) { return; }
    remember(resizing.node, true);
    resizing = null;
  });

  // --- "Perfekt verteilen" (D-054): several layouts, the one with the fewest crossings and overlaps wins ---
  function crosses(p1, p2, p3, p4) {
    function side(a, b, c) { return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x); }
    return side(p1, p2, p3) * side(p1, p2, p4) < 0 && side(p3, p4, p1) * side(p3, p4, p2) < 0;
  }
  function score() {
    var lines = cy.edges().filter(function (e) { return e.data("type") !== "ghost"; }).map(function (e) {
      return [e.source(), e.target(), e.source().position(), e.target().position()];
    });
    var bad = 0;
    for (var i = 0; i < lines.length; i++) {
      for (var j = i + 1; j < lines.length; j++) {
        var a = lines[i], b = lines[j];
        if (a[0] === b[0] || a[0] === b[1] || a[1] === b[0] || a[1] === b[1]) { continue; }
        if (crosses(a[2], a[3], b[2], b[3])) { bad += 1; }
      }
    }
    var boxes = cy.nodes().map(function (n) { return n.boundingBox({ includeLabels: false }); });
    for (var k = 0; k < boxes.length; k++) {
      for (var m = k + 1; m < boxes.length; m++) {
        var p = boxes[k], q = boxes[m];
        if (p.x1 < q.x2 && q.x1 < p.x2 && p.y1 < q.y2 && q.y1 < p.y2) { bad += 5; }
      }
    }
    return bad;
  }
  document.querySelector("[data-arrange]").addEventListener("click", function (event) {
    if (!window.confirm(event.currentTarget.getAttribute("data-confirm"))) { return; }
    var start = {};
    cy.nodes().forEach(function (n) { start[n.id()] = { x: n.position("x"), y: n.position("y") }; });
    var best = null, bestScore = Infinity;
    for (var run = 0; run < 6; run++) {
      cy.layout(layoutOptions(null)).run();
      var s = score();
      if (s < bestScore) {
        bestScore = s;
        best = {};
        cy.nodes().forEach(function (n) { best[n.id()] = { x: n.position("x"), y: n.position("y") }; });
      }
    }
    // Glide from where the boxes were to the winning arrangement, then remember it.
    cy.nodes().forEach(function (n) { n.position(start[n.id()]); });
    var moves = cy.nodes().map(function (n) {
      return n.animation({ position: best[n.id()], duration: 500, easing: "ease-in-out-cubic" }).play().promise();
    });
    Promise.all(moves).then(function () {
      nudge();
      cy.animate({ fit: { padding: 30 } }, { duration: 250 });
      remember(cy.nodes(), false);
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
    if (first) { first.removeClass("focus"); }
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
      node.addClass("focus");
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
