// Forms with data-confirm ask before they are sent (D-062: deleting a glossary term).
(function () {
  "use strict";
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) { event.preventDefault(); }
    });
  });
})();
