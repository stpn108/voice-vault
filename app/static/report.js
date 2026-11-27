// Copy the report as plain text (REQ-014, D-084). Where the clipboard is not available, the list is selected
// so the owner can copy it himself. The button stays hidden without this script.
(function () {
  "use strict";
  var button = document.querySelector("[data-copy]");
  if (!button) { return; }
  var label = button.textContent;
  function tell(text) {
    button.textContent = text;
    window.setTimeout(function () { button.textContent = label; }, 2000);
  }
  function select() {
    var range = document.createRange();
    range.selectNodeContents(document.querySelector(".report"));
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    tell(button.getAttribute("data-failed"));
  }
  button.addEventListener("click", function () {
    if (!navigator.clipboard || !window.isSecureContext) { select(); return; }
    navigator.clipboard.writeText(button.getAttribute("data-copy"))
      .then(function () { tell(button.getAttribute("data-done")); }, select);
  });
  button.hidden = false;
})();
