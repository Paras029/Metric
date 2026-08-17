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
  // Two ways in, and they answer different questions.
  //
  // **Hovering** is for scanning the graph itself: one hop, lasting as long as the pointer does.
  // Lighting everything reachable from a box near the start lights the whole graph and answers
  // nothing, so it deliberately stops at the arrows that touch what is under the pointer.
  //
  // **Opening a scenario** is the one that matters, and it is why any of this exists. A scenario
  // *is* a route through this graph -- that is the premise of the whole tool -- but on screen the
  // two were separate things: a card with words on it, and a picture with no way to ask which of
  // its arrows that card was describing. Opening a card now lights exactly the route it walks, and
  // several open cards light several routes at once, which is how two scenarios are compared: not
  // by reading both descriptions and holding the difference in your head, but by seeing where the
  // two paths part. Collapsing a card takes its route back down. Nothing else is a selector, so
  // the list is the control and the graph is the readout.
  var selectionNote = document.querySelector('[data-graph-selection]');
  var clearButton = document.querySelector('[data-graph-clear]');
  var section = document.querySelector('[data-graph-section]');
  var noteDefault = selectionNote ? selectionNote.textContent : '';

  var lit = [];
  var open = [];                 // the scenario cards currently holding a route lit, in order

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

  function parts(from, to, outcome) {
    // An arrow and its own label are two elements standing for one thing, and both have to light
    // together or a lit route is drawn with unlit words on it. The outcome narrows it further
    // where it is known: two outcomes of one decision routinely land on the same next decision,
    // and lighting both would say a scenario took a branch it did not take.
    var selector = '[data-source="' + from + '"][data-target="' + to + '"]';
    if (outcome !== undefined && outcome !== null) {
      selector += '[data-outcome="' + outcome + '"]';
    }
    return svg.querySelectorAll(selector);
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

  function route(card) {
    try { return JSON.parse(card.getAttribute('data-route')); } catch (error) { return null; }
  }

  function paint() {
    // Every open card's route, redrawn from scratch. Adding and removing one card's elements
    // incrementally would need a count per element, because two routes overlap wherever they
    // share a prefix -- and they nearly always share a prefix.
    darken();
    if (!open.length) { return; }
    svg.classList.add('graph--focused');
    open.forEach(function (card) {
      var walked = route(card);
      if (!walked) { return; }
      walked.edges.forEach(function (edge) {
        Array.prototype.forEach.call(parts(edge[0], edge[1], edge[2]), function (part) {
          light(part, true);
        });
      });
      walked.nodes.forEach(function (id) { light(node(id), true); });
    });
  }

  function announce() {
    if (clearButton) { clearButton.hidden = !open.length; }
    if (!selectionNote) { return; }
    if (!open.length) { selectionNote.textContent = noteDefault; return; }
    var named = open.map(function (card) { return card.getAttribute('data-route-label'); });
    selectionNote.textContent = named.length === 1
      ? ('Showing the route ' + named[0] + ' walks')
      : ('Showing ' + named.length + ' routes: ' + named.join(', '));
  }

  function refresh() {
    open = Array.prototype.filter.call(
      document.querySelectorAll('[data-route]'), function (card) { return card.open; });
    paint();
    announce();
  }

  // A card opened while the graph is folded away would light a route nobody can see, which reads
  // as the feature not working. Unfolding on the first one is the direct consequence of what was
  // clicked rather than a surprise -- and it is not forced open again afterwards, so somebody who
  // folds it back keeps it folded while they work through the list.
  document.addEventListener('toggle', function (event) {
    var card = event.target;
    if (!card.hasAttribute || !card.hasAttribute('data-route')) { return; }
    if (card.open && section && !section.open) { section.open = true; fit(); }
    refresh();
  }, true);

  svg.addEventListener('mouseover', function (event) {
    if (open.length) { return; }               // a held route outranks whatever is under the
    var el = event.target.closest &&           // pointer, or moving to read it would lose it
      event.target.closest('.graph__node, .graph__edge, .graph__label');
    if (!el) { return; }
    darken();
    svg.classList.add('graph--focused');
    if (el.hasAttribute('data-node')) { focusNode(el); } else { focusEdge(el); }
  });

  svg.addEventListener('mouseleave', function () {
    if (!open.length) { darken(); }
  });

  function closeAll() {
    // Closing the cards rather than only darkening the graph: the open cards *are* the selection,
    // and a Clear that left them open would put the control and the readout out of step.
    open.slice().forEach(function (card) { card.open = false; });
    refresh();
  }

  if (clearButton) { clearButton.addEventListener('click', closeAll); }
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && open.length) { closeAll(); }
  });

  refresh();
})();

/* Two views of one agent: capabilities collapsed, and every decision.
 *
 * The collapsed view is what a reader orients by, so it is shown first wherever spans have been
 * drawn. Opening a block switches to the detailed graph with that block's boxes lit and the rest
 * dimmed -- which is what makes the detail readable at all on a real use case, where the whole
 * picture is forty boxes and only a tenth of them are the block being looked at. */
(function () {
  var canvas = document.querySelector('[data-graph-canvas]');
  if (!canvas) { return; }
  var blocks = canvas.querySelector('[data-graph-view="blocks"]');
  var detail = canvas.querySelector('[data-graph-view="detail"]');
  var toggle = document.querySelector('[data-graph-detail]');
  if (!blocks || !detail || !toggle) { return; }

  function show(which, capability) {
    var wantDetail = which === 'detail';
    blocks.hidden = wantDetail;
    detail.hidden = !wantDetail;
    toggle.textContent = wantDetail ? 'Show capabilities' : 'Show every decision';

    // Dimming rather than hiding: a block's boxes mean nothing without the ones they lead to,
    // and removing the rest would leave arrows pointing off the edge of the drawing.
    var first = null;
    detail.querySelectorAll('[data-node]').forEach(function (node) {
      var owner = node.getAttribute('data-capability') || '';
      var mine = !capability || owner === capability;
      node.classList.toggle('graph__node--faded', !mine);
      if (mine && capability && !first) { first = node; }
    });

    // And brought into view. On a real graph the block just opened is usually below the fold, so
    // without this the click lands on a screen of faded boxes and reads as the drawing greying
    // itself out for no reason.
    if (first && first.scrollIntoView) {
      first.scrollIntoView({ block: 'center', inline: 'center' });
    }
  }

  toggle.addEventListener('click', function () {
    show(detail.hidden ? 'detail' : 'blocks', '');
  });

  blocks.querySelectorAll('[data-capability]').forEach(function (node) {
    node.style.cursor = 'pointer';
    node.addEventListener('click', function () {
      show('detail', node.getAttribute('data-capability'));
    });
  });
})();
