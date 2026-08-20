// Ask before something irreversible.
//
// Deliberately rare. A confirmation on every destructive-looking action is one people learn to
// click through, and by the time it guards something that matters it has already been trained
// away. So it is attached by hand, on the two actions in this tool that genuinely cannot be
// undone: deleting a workspace, and starting a stage from nothing.
//
// Everything else that removes something is either reversible (a staged edit in the declaration
// editor, which is why that file strips the attribute from its own buttons) or leaves the thing
// it removed somewhere it can be put back from.
//
// The message names what is about to go rather than asking "are you sure?", which is a question
// nobody can answer without already knowing.
(function () {
  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('[data-confirm]');
    if (!button) { return; }
    // Whitespace from a wrapped template attribute reads as a paragraph break in the dialog.
    var message = button.getAttribute('data-confirm').replace(/\s+/g, ' ').trim();
    if (!window.confirm(message)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);
})();
