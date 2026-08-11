// Reading the declared graph: zoom, pan, and following a thread through it.
//
// A separate file rather than inline script because the same behaviour has to run in two places
// -- on the stage page, and inside the self-contained copy the download route produces. A graph
// downloaded as a dead picture is a graph nobody can read, so the file is inlined there verbatim
// and the two cannot drift.
//
// Everything here is progressive. With scripting off the graph still renders at its natural size
// inside a scrolling frame, which is what it did before any of this existed.
(function () {
  var frame = document.querySelector('[data-graph]');
  var canvas = document.querySelector('[data-graph-canvas]');
  var readout = document.querySelector('[data-zoom-level]');
  if (!frame || !canvas) { return; }

  var svg = canvas.querySelector('svg');
  var natural = svg ? svg.getAttribute('width') : 0;
  var scale = 1;

  function apply() {
    canvas.style.transform = 'scale(' + scale + ')';
    canvas.style.transformOrigin = '0 0';
    if (svg) {
      // The wrapper has to grow with the drawing or the frame scrolls over empty space.
      canvas.style.width = (natural * scale) + 'px';
      canvas.style.height = (svg.getAttribute('height') * scale) + 'px';
    }
    if (readout) { readout.textContent = Math.round(scale * 100) + '%'; }
  }

  function fit() {
    scale = natural ? Math.min(1, (frame.clientWidth - 24) / natural) : 1;
    apply();
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-zoom]'), function (button) {
    button.addEventListener('click', function () {
      var how = button.dataset.zoom;
      if (how === 'fit') { fit(); return; }
      scale = Math.min(3, Math.max(0.2, scale * (how === 'in' ? 1.25 : 0.8)));
      apply();
    });
  });

  // Drag distance is tracked as well as drag state, because panning and selecting share the same
  // gesture start. Without it, every pan that finishes over a box also selects that box.
  var dragging = false, startX = 0, startY = 0, fromLeft = 0, fromTop = 0, travelled = 0;
  frame.addEventListener('mousedown', function (event) {
    dragging = true;
    travelled = 0;
    startX = event.clientX; startY = event.clientY;
    fromLeft = frame.scrollLeft; fromTop = frame.scrollTop;
    frame.classList.add('graph__frame--dragging');
    event.preventDefault();
  });
  window.addEventListener('mousemove', function (event) {
    if (!dragging) { return; }
    travelled = Math.max(travelled, Math.abs(event.clientX - startX),
                         Math.abs(event.clientY - startY));
    frame.scrollLeft = fromLeft - (event.clientX - startX);
    frame.scrollTop = fromTop - (event.clientY - startY);
  });
  window.addEventListener('mouseup', function () {
    dragging = false;
    frame.classList.remove('graph__frame--dragging');
  });

  fit();

  if (!svg) { return; }

  // Paint order in SVG is document order, and a crowded row can leave a box or an edge label
  // sitting underneath a neighbour with nothing to click to fix it. Moving the hovered element to
  // the end of its parent's children is enough to bring it to the front for as long as the
  // pointer is over it -- no state to track, and the next thing hovered does the same.
  svg.addEventListener('mouseover', function (event) {
    var el = event.target.closest &&
      event.target.closest('.graph__node, .graph__edge, .graph__label');
    if (el && el.parentNode) { el.parentNode.appendChild(el); }
  });

  // Following one thread. A declared graph of any size is a picture where every line crosses every
  // other, and the question a reader has is never "what is the whole shape" -- it is "where does
  // *this* one come from and where does it go". Holding that thread at full strength and dropping
  // the rest back reads the answer straight off the page instead of asking somebody to trace it
  // with a finger.
  //
  // Two ways in, because they answer different questions. Hovering is for scanning: it lasts as
  // long as the pointer does and shows one hop, since lighting everything reachable from a box
  // near the start lights the whole graph and answers nothing. Clicking is for stopping: the
  // highlight stays put, which is what reading the box's text against the intake, or pointing at
  // it while talking to somebody else, actually needs. "Whole branch" then widens a *selection*
  // to the entire route through it -- everything that can reach it and everything it can reach --
  // which is the shape a scenario walks, and is only ever asked for deliberately.
  var branchMode = document.querySelector('[data-graph-branch]');
  var selectionNote = document.querySelector('[data-graph-selection]');
  var clearButton = document.querySelector('[data-graph-clear]');
  var noteDefault = selectionNote ? selectionNote.textContent : '';

  var lit = [];
  var pinned = null;            // the element a click fixed the highlight on, if any

  function light(el, source) {
    if (!el) { return; }
    el.classList.add('is-lit');
    if (source) { el.classList.add('is-lit--source'); }
    lit.push(el);
  }

  function darken() {
    svg.classList.remove('graph--focused');
    lit.forEach(function (el) {
      el.classList.remove('is-lit');
      el.classList.remove('is-lit--source');
    });
    lit = [];
  }

  function node(id) { return svg.querySelector('[data-node="' + id + '"]'); }

  function joining(id) {
    return svg.querySelectorAll('[data-source="' + id + '"], [data-target="' + id + '"]');
  }

  function parts(from, to) {
    // An arrow and its own label are two elements standing for one thing, and both have to move
    // together or a lit route is drawn with unlit words on it.
    return svg.querySelectorAll('[data-source="' + from + '"][data-target="' + to + '"]');
  }

  function focusNode(el) {
    var id = el.getAttribute('data-node');
    light(el, true);
    Array.prototype.forEach.call(joining(id), function (line) {
      light(line, false);
      // The box at the far end of each arrow, so this answers "from where, to where" rather than
      // only "which arrows touch this".
      var other = line.getAttribute('data-source') === id
        ? line.getAttribute('data-target') : line.getAttribute('data-source');
      light(node(other), false);
    });
  }

  function focusEdge(el) {
    var from = el.getAttribute('data-source'), to = el.getAttribute('data-target');
    Array.prototype.forEach.call(parts(from, to), function (part) { light(part, part === el); });
    light(node(from), false);
    light(node(to), false);
  }

  function walk(seeds, attribute, opposite) {
    // Breadth-first from the seeds, lighting each hop as it is crossed. Edges are lit during the
    // walk rather than by "both ends are lit" afterwards, which would also light every crossing
    // line that happens to join two boxes on the route without being part of it.
    var seen = {}, queue = seeds.slice();
    seeds.forEach(function (id) { seen[id] = true; });
    while (queue.length) {
      var id = queue.shift();
      Array.prototype.forEach.call(
        svg.querySelectorAll('[' + attribute + '="' + id + '"]'), function (line) {
          var next = line.getAttribute(opposite);
          var from = line.getAttribute('data-source'), to = line.getAttribute('data-target');
          Array.prototype.forEach.call(parts(from, to), function (part) { light(part, false); });
          if (next && !seen[next]) {
            seen[next] = true;
            light(node(next), false);
            queue.push(next);
          }
        });
    }
  }

  function focusBranch(el) {
    var seeds;
    if (el.hasAttribute('data-node')) {
      seeds = [el.getAttribute('data-node')];
      light(el, true);
    } else {
      var from = el.getAttribute('data-source'), to = el.getAttribute('data-target');
      seeds = [from, to];
      Array.prototype.forEach.call(parts(from, to), function (part) { light(part, true); });
      light(node(from), true);
      light(node(to), true);
    }
    walk(seeds, 'data-source', 'data-target');
    walk(seeds, 'data-target', 'data-source');
  }

  function focus(el, branch) {
    darken();
    svg.classList.add('graph--focused');
    if (branch) { focusBranch(el); }
    else if (el.hasAttribute('data-node')) { focusNode(el); }
    else { focusEdge(el); }
  }

  function describe(el) {
    if (el.hasAttribute('data-node')) { return el.getAttribute('data-node'); }
    return el.getAttribute('data-source') + ' → ' + el.getAttribute('data-target');
  }

  function announce() {
    if (clearButton) { clearButton.hidden = !pinned; }
    if (!selectionNote) { return; }
    selectionNote.textContent = pinned
      ? ('Holding ' + describe(pinned) +
         (branchMode && branchMode.checked ? ' and its whole branch' : '') +
         ' · click it again to release')
      : noteDefault;
  }

  function unpin() {
    pinned = null;
    darken();
    announce();
  }

  function target(event) {
    return event.target.closest &&
      event.target.closest('.graph__node, .graph__edge, .graph__label');
  }

  function same(a, b) {
    if (!a || !b) { return false; }
    if (a.hasAttribute('data-node') || b.hasAttribute('data-node')) {
      return a.getAttribute('data-node') === b.getAttribute('data-node');
    }
    return a.getAttribute('data-source') === b.getAttribute('data-source')
      && a.getAttribute('data-target') === b.getAttribute('data-target');
  }

  svg.addEventListener('mouseover', function (event) {
    if (pinned) { return; }                    // a held selection outranks whatever is under the
    var el = target(event);                    // pointer, or moving to read it would lose it
    if (!el) { return; }
    focus(el, false);
  });

  svg.addEventListener('mouseleave', function () {
    if (!pinned) { darken(); }
  });

  svg.addEventListener('click', function (event) {
    if (travelled > 4) { return; }             // that was a pan, not a click
    var el = target(event);
    if (!el) { unpin(); return; }              // clicking the background is the obvious release
    if (same(el, pinned)) { unpin(); return; }
    pinned = el;
    focus(el, branchMode && branchMode.checked);
    announce();
  });

  if (branchMode) {
    branchMode.addEventListener('change', function () {
      if (pinned) { focus(pinned, branchMode.checked); announce(); }
    });
  }

  if (clearButton) { clearButton.addEventListener('click', unpin); }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && pinned) { unpin(); }
  });

  announce();
})();
