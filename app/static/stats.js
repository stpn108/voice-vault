// Statistics page (D-035): draws the four charts with Chart.js from the JSON block the server wrote.
(function () {
  "use strict";
  var source = document.getElementById("stats-data");
  if (!source || !window.Chart) { return; }
  var payload = JSON.parse(source.textContent);
  var data = payload.data;
  var text = payload.text;
  var css = getComputedStyle(document.documentElement);
  function v(name) { return css.getPropertyValue(name).trim(); }

  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = v("--muted");
  Chart.defaults.borderColor = v("--line");
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.plugins.tooltip.mode = "index";
  Chart.defaults.plugins.tooltip.intersect = false;

  // Thin bars, 4px rounded at the data end, square at the baseline, a surface gap between fills.
  function bars(label, values, color) {
    return { label: label, data: values, backgroundColor: color, maxBarThickness: 24, borderRadius: 4,
             borderSkipped: "start", borderColor: v("--surface"), borderWidth: 1 };
  }
  var grid = { color: v("--line") };

  new Chart(document.getElementById("chart-flow"), {
    type: "bar",
    data: { labels: data.labels, datasets: [bars(text.created, data.flow.created, v("--series-1")),
                                            bars(text.finished, data.flow.finished, v("--series-2"))] },
    options: { scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 }, grid: grid } },
               plugins: { legend: { position: "top", align: "start" } } }
  });

  var priorityColor = { "1": v("--danger"), "2": v("--warn"), "3": v("--accent"), "4": v("--faint") };
  new Chart(document.getElementById("chart-priority"), {
    type: "bar",
    data: { labels: data.labels, datasets: ["1", "2", "3", "4"].map(function (p) {
      var set = bars(text.priorities[p], data.open_by_priority[p], priorityColor[p]);
      set.borderRadius = 0;
      set.borderWidth = { top: 2 };
      set.stack = "open";
      return set;
    }) },
    options: { scales: { x: { stacked: true, grid: { display: false } },
                         y: { stacked: true, beginAtZero: true, ticks: { precision: 0 }, grid: grid } },
               plugins: { legend: { position: "top", align: "start" } } }
  });

  var people = document.getElementById("chart-people");
  if (people) {
    new Chart(people, {
      type: "bar",
      data: { labels: data.people.names, datasets: [bars(text.open, data.people.open, v("--series-1"))] },
      options: { indexAxis: "y", plugins: { legend: { display: false } },
                 scales: { x: { beginAtZero: true, ticks: { precision: 0 }, grid: grid }, y: { grid: { display: false } } } }
    });
  }

  new Chart(document.getElementById("chart-hours"), {
    type: "bar",
    data: { labels: data.labels, datasets: [bars(text.hours, data.hours, v("--series-1"))] },
    options: { scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: grid } },
               plugins: { legend: { display: false } } }
  });
})();
