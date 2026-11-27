// The report page (REQ-013, REQ-014, D-085): picking a person shows their report at once, and the icon at the
// top right copies the report as plain text. Where the clipboard is not available, the lists are selected so the
// owner can copy them himself. The icon stays hidden without this script.
(function () {
  "use strict";
  var form = document.querySelector("form[data-report]");
  if (form) {
    form.querySelector("select").addEventListener("change", function () { form.requestSubmit(); });
  }
  var button = document.querySelector("button[data-copy]");
  if (!button) { return; }
  var card = button.closest(".report");
  var note = card.querySelector(".copy-note");
  var timer = 0;
  function tell(text, copied) {
    note.textContent = text;
    button.classList.toggle("done", copied);
    window.clearTimeout(timer);
    timer = window.setTimeout(function () { note.textContent = ""; button.classList.remove("done"); }, 2000);
  }
  function select() {
    var range = document.createRange();
    range.setStartBefore(card.querySelector("h2"));
    range.setEndAfter(card.lastElementChild);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    tell(button.getAttribute("data-failed"), false);
  }
  button.addEventListener("click", function () {
    if (!navigator.clipboard || !window.isSecureContext) { select(); return; }
    navigator.clipboard.writeText(button.getAttribute("data-copy"))
      .then(function () { tell(button.getAttribute("data-done"), true); }, select);
  });
  button.hidden = false;
})();
