// Graph of the tasks and their links (D-047), drawn with Cytoscape.js from the JSON block the server wrote.
(function () {
  "use strict";
  var source = document.getElementById("graph-data");
  if (!source || !window.cytoscape) { return; }
  var fcose = Boolean(window.cytoscapeFcose);
  var withTopics = JSON.parse(source.textContent).data.nodes.some(function (n) { return n.kind === "topic"; });
  if (fcose) { cytoscape.use(window.cytoscapeFcose); }
  var payload = JSON.parse(source.textContent);
  var css = getComputedStyle(document.documentElement);
  function v(name) { return css.getPropertyValue(name).trim(); }

  var font = getComputedStyle(document.body).fontFamily;
  // Status fills the node; an urgent or high priority shows as a coloured rim (D-050).
  var fill = { open: v("--surface"), in_progress: v("--progress"), done: v("--ok"), dropped: v("--hover"), topic: v("--accent") };
  var rim = { 1: v("--danger"), 2: v("--warn") };
  var elements = payload.data.nodes.map(function (n) {
    return { data: { id: n.id, label: n.label.length > 70 ? n.label.slice(0, 67) + "…" : n.label, full: n.label,
                     status: n.status, kind: n.kind, url: n.url, priority: n.priority || 3 } };
  }).concat(payload.data.edges.map(function (e, i) {
    return { data: { id: "e" + i, source: e.source, target: e.target, type: e.type } };
  }));

  function textWidth(n) { return Math.min(210, Math.max(70, (n.data("label") || "").length * 7.2)); }
  function textLines(n) { return Math.max(1, Math.ceil((n.data("label") || "").length * 7.2 / 210)); }

  var cy = cytoscape({
    container: document.getElementById("graph"),
    elements: elements,
    minZoom: 0.25,
    maxZoom: 3,
    style: [
      { selector: "node", style: {
        "label": "data(label)", "text-wrap": "wrap", "text-max-width": "210px", "font-size": "13px",
        "font-family": font, "font-weight": 500, "color": v("--text"), "line-height": 1.3,
        "text-valign": "center", "text-halign": "center", "shape": "round-rectangle",
        "width": textWidth, "height": function (n) { return 17 * textLines(n); }, "padding": "12px",
        "background-color": function (n) { return fill[n.data("status")] || fill.open; },
        "border-width": function (n) { return rim[n.data("priority")] && n.data("status") !== "done" ? 2.5 : 1.2; },
        "border-color": function (n) { return rim[n.data("priority")] || v("--line"); },
        "transition-property": "opacity", "transition-duration": "120ms" } },
      { selector: "node[kind = 'waiting']", style: { "border-style": "dashed", "border-width": 1.8, "border-color": v("--muted") } },
      { selector: "node[status = 'done'], node[status = 'dropped']", style: { "opacity": 0.6, "color": v("--muted") } },
      { selector: "node[status = 'in_progress']", style: { "color": "#1a1300", "font-weight": 600 } },
      { selector: "node[status = 'topic']", style: { "font-size": "15px", "font-weight": 700, "color": v("--surface"),
        "shape": "round-rectangle", "padding": "16px", "border-width": 0,
        "width": function (n) { return Math.max(80, (n.data("label") || "").length * 8.6); }, "height": 20 } },
      { selector: "edge", style: { "width": 2.5, "line-color": v("--accent"), "curve-style": "unbundled-bezier",
        "control-point-distances": 18, "control-point-weights": 0.5, "opacity": 0.8,
        "transition-property": "opacity", "transition-duration": "120ms" } },
      { selector: "edge[type = 'topic']", style: { "width": 1.5, "line-color": v("--faint"), "line-style": "dotted",
        "curve-style": "straight", "opacity": 0.9 } },
      { selector: "edge[type = 'link']:active, edge[type = 'link']:selected", style: { "line-color": v("--danger"), "opacity": 1 } },
      { selector: ".faded", style: { "opacity": 0.12 } },
      { selector: ".deg2", style: { "opacity": 0.75 } },
      { selector: ".deg3", style: { "opacity": 0.5 } },
      { selector: "node.focus", style: { "border-width": 3, "border-color": v("--accent") } },
      { selector: "node.ghost", style: { "width": 1, "height": 1, "opacity": 0, "label": "", "events": "no" } },
      { selector: "edge[type = 'ghost']", style: { "width": 2, "line-color": v("--accent"), "line-style": "dashed",
        "curve-style": "straight", "events": "no" } }
    ],
    // fCoSE (D-050): compact, without overlaps; separate groups are packed side by side.
    layout: fcose ? { name: "fcose", quality: "proof", animate: false, randomize: true, nodeDimensionsIncludeLabels: true,
                      packComponents: true, tile: true, tilingPaddingVertical: 24, tilingPaddingHorizontal: 24,
                      nodeSeparation: withTopics ? 70 : 45, nodeRepulsion: function () { return withTopics ? 8000 : 5200; },
                      idealEdgeLength: function (e) { return e.data("type") === "topic" ? 45 : 75; },
                      edgeElasticity: function (e) { return e.data("type") === "topic" ? 0.6 : 0.35; },
                      gravity: 0.3, gravityRange: 2.5, numIter: 4000, padding: 30 }
                  : { name: "cose", animate: false, nodeDimensionsIncludeLabels: true, padding: 40 }
  });

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
