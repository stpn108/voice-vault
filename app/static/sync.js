// "Jetzt abholen" (D-045): while the fetch runs, ask the server every few seconds; when it is through,
// hide the notice, or reload the list if new recordings came in.
(function () {
  "use strict";
  var notice = document.getElementById("sync-notice");
  if (!notice) { return; }
  var tries = 0;

  function check() {
    tries += 1;
    fetch("/sync/status", { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (status) {
        if (status.state === "done") {
          if (status.new > 0) {
            notice.textContent = notice.getAttribute("data-done-new");
            setTimeout(function () { window.location.reload(); }, 800);
          } else {
            notice.textContent = notice.getAttribute("data-done");
            setTimeout(function () { notice.hidden = true; }, 3000);
          }
        } else if (status.state === "idle") {
          notice.hidden = true;  // given up (the app container did not answer in time)
        } else if (tries < 200) {
          setTimeout(check, 3000);
        }
      })
      .catch(function () { if (tries < 200) { setTimeout(check, 5000); } });
  }

  setTimeout(check, 3000);
})();
