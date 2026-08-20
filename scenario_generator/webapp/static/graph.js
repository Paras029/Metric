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

  // Every drawing in the canvas, not the first one. There are two -- capabilities collapsed, and
  // every decision -- and binding to whichever came first left the other with no zoom, no pan and
  // no hover at all. It read as the graph going dead the moment the view was switched.
  function drawings() {
    return Array.prototype.slice.call(canvas.querySelectorAll('svg'));
  }

  function showing() {
    var found = null;
    drawings().forEach(function (candidate) {
      // A view is hidden by the [hidden] attribute on its wrapper rather than by anything on the
      // drawing itself, so measuring it is what actually answers "is this the one on screen".
      if (!found && candidate.getBoundingClientRect().width > 0) { found = candidate; }
    });
    return found || drawings()[0] || null;
  }

  var scale = 1;

  function apply() {
    var svg = showing();
    canvas.style.transform = 'scale(' + scale + ')';
    canvas.style.transformOrigin = '0 0';
    if (svg) {
      // The wrapper has to grow with the drawing or the frame scrolls over empty space.
      canvas.style.width = (svg.getAttribute('width') * scale) + 'px';
      canvas.style.height = (svg.getAttribute('height') * scale) + 'px';
    }
    if (readout) { readout.textContent = Math.round(scale * 100) + '%'; }
  }

  function fit() {
    var svg = showing();
    var natural = svg ? svg.getAttribute('width') : 0;
    scale = natural ? Math.min(1, (frame.clientWidth - 24) / natural) : 1;
    apply();
  }

  // The two drawings are different sizes, so a view switch has to refit or the frame scrolls over
  // empty space on the smaller one and clips the larger.
  canvas.addEventListener('metric:viewchanged', fit);

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
  window.addEventListener('resize', fit);

  if (!drawings().length) { return; }

  // Paint order in SVG is document order, and a crowded row can leave a box or an edge label
  // sitting underneath a neighbour with nothing to click to fix it. Moving the hovered element to
  // the end of its parent's children is enough to bring it to the front for as long as the
  // pointer is over it -- no state to track, and the next thing hovered does the same.
  canvas.addEventListener('mouseover', function (event) {
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
    drawings().forEach(function (one) { one.classList.remove('graph--focused'); });
    lit.forEach(function (el) {
      el.classList.remove('is-lit');
      el.classList.remove('is-lit--source');
    });
    lit = [];
  }

  // Every lookup runs across both drawings. A route lit only in the one that happens to be on
  // screen goes dark the moment the view is switched, which is the opposite of what the two views
  // are for: the whole point of switching is to see the same thing at a different altitude.
  function all(selector) {
    var found = [];
    drawings().forEach(function (one) {
      Array.prototype.push.apply(found, one.querySelectorAll(selector));
    });
    return found;
  }

  // Quoted attribute values, so the only characters that need escaping are the quote and the
  // backslash. Not CSS.escape: that escapes for *identifiers*, and would turn the colon in an
  // ending's id (CAP-01:S-04) into something that matches nothing.
  function quoted(value) {
    return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function node(id) { return all('[data-node="' + quoted(id) + '"]'); }

  function joining(id) {
    var safe = quoted(id);
    return all('[data-source="' + safe + '"], [data-target="' + safe + '"]');
  }

  function parts(from, to, outcome) {
    // An arrow and its own label are two elements standing for one thing, and both have to light
    // together or a lit route is drawn with unlit words on it. The outcome narrows it further
    // where it is known: two outcomes of one decision routinely land on the same next decision,
    // and lighting both would say a scenario took a branch it did not take.
    var selector = '[data-source="' + quoted(from) + '"][data-target="' + quoted(to) + '"]';
    if (outcome !== undefined && outcome !== null) {
      selector += '[data-outcome="' + quoted(outcome) + '"]';
    }
    return all(selector);
  }

  function lightAll(elements, source) {
    elements.forEach(function (el) { light(el, source); });
  }

  function focusNode(el) {
    var id = el.getAttribute('data-node');
    light(el, true);
    joining(id).forEach(function (line) {
      light(line, false);
      // The box at the far end of each arrow, so this answers "from where, to where" rather than
      // only "which arrows touch this".
      var other = line.getAttribute('data-source') === id
        ? line.getAttribute('data-target') : line.getAttribute('data-source');
      lightAll(node(other), false);
    });
  }

  function focusEdge(el) {
    var from = el.getAttribute('data-source'), to = el.getAttribute('data-target');
    parts(from, to).forEach(function (part) { light(part, part === el); });
    lightAll(node(from), false);
    lightAll(node(to), false);
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
    drawings().forEach(function (one) { one.classList.add('graph--focused'); });
    open.forEach(function (card) {
      var walked = route(card);
      if (!walked) { return; }
      walked.edges.forEach(function (edge) {
        parts(edge[0], edge[1], edge[2]).forEach(function (part) { light(part, true); });
      });
      walked.nodes.forEach(function (id) { lightAll(node(id), true); });
      // And the capability block, in the collapsed drawing. A scenario walks one block, and
      // without this, opening a card while the collapsed view is showing lights nothing -- which
      // reads as the highlighting being broken rather than as the two drawings speaking different
      // languages. The block's own endings light with it, so what is lit is the whole of what the
      // scenario can reach.
      if (walked.block) {
        lightAll(node(walked.block), true);
        joining(walked.block).forEach(function (line) {
          if (line.getAttribute('data-outcome') !== 'ends') { return; }
          light(line, true);
          lightAll(node(line.getAttribute('data-target')), true);
        });
      }
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

  // Bound to the canvas rather than to one drawing, so following a thread works in the collapsed
  // view exactly as it does in the detailed one. Hovering a capability block lights what hands on
  // to it and what it hands on to, which is the question that view exists to answer.
  canvas.addEventListener('mouseover', function (event) {
    // Anything deliberately held outranks whatever happens to be under the pointer. Two things
    // can hold: an open scenario card here, and a selected row in the declaration editor, which
    // sets the class below. Without the second, selecting a decision lit it and then lost it the
    // moment the pointer crossed the drawing to look at what had lit -- which is the one movement
    // the highlight exists to support.
    if (open.length || document.body.classList.contains('is-holding')) { return; }
    var el = event.target.closest &&
      event.target.closest('.graph__node, .graph__edge, .graph__label');
    if (!el) { return; }
    darken();
    var within = el.closest('svg');
    if (within) { within.classList.add('graph--focused'); }
    if (el.hasAttribute('data-node')) { focusNode(el); } else { focusEdge(el); }
  });

  canvas.addEventListener('mouseleave', function () {
    if (!open.length && !document.body.classList.contains('is-holding')) { darken(); }
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

  // Whether there is a collapsed drawing to switch to. A declaration with no spans has the
  // wrapper but nothing in it, and offering a view of nothing is worse than not offering one --
  // so the control appears when a drawing does, which is what happens the moment somebody draws
  // a span in the editor and previews it.
  function collapsible() { return !!blocks.querySelector('svg'); }

  function offer() {
    toggle.hidden = !collapsible();
    if (!collapsible() && blocks.hidden === false) { show('detail', ''); return; }
    // And the right label. A control that appears saying "Show every decision" while every
    // decision is already what is on screen is a control nobody can predict.
    toggle.textContent = detail.hidden ? 'Show every decision' : 'Show capabilities';
  }
  canvas.addEventListener('metric:drawingschanged', offer);

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

    // The two drawings are different sizes, so whatever is holding the zoom has to refit. Sent
    // as an event rather than called directly because the zoom lives in another closure, and the
    // alternative is one of them reaching into the other.
    canvas.dispatchEvent(new CustomEvent('metric:viewchanged'));

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
  offer();

  // Bound to the container rather than to each box, because the drawing is replaced wholesale
  // whenever an edit is previewed or saved -- and handlers attached to the boxes that were there
  // at load would be attached to elements nothing on screen contains any more.
  blocks.addEventListener('click', function (event) {
    var node = event.target.closest && event.target.closest('[data-capability]');
    if (node) { show('detail', node.getAttribute('data-capability')); }
  });
  blocks.style.cursor = 'pointer';
})();

/* The graph at the size it needs, with the scenarios beside it.
 *
 * A declaration of any size draws to a picture taller than the frame it sits in, and zoom does
 * not fix that -- a graph scaled to fit a paragraph-high frame is unreadable at the scale that
 * fits. What it needs is the window.
 *
 * And where the page has a scenario space, the list comes with it. The premise of the route
 * highlighting is that a scenario *is* a path through the drawing, which only pays off if both
 * are in front of you: on the stage, opening a card lights a route several screens away, so
 * reading one scenario against the picture means scrolling up and back for every card.
 *
 * The sections are **moved** rather than copied. A copy would be a second drawing needing its own
 * zoom, its own hover and its own route lighting, and the two would disagree the first time
 * either was touched. Moving keeps every listener, every open card and every lit element exactly
 * as they were, so the overlay is the same page at a different size rather than another one. */
(function () {
  var overlay = document.querySelector('[data-expand]');
  var opener = document.querySelector('[data-expand-open]');
  if (!overlay || !opener) { return; }

  var closer = overlay.querySelector('[data-expand-close]');
  var note = overlay.querySelector('[data-expand-note]');
  var canvas = document.querySelector('[data-graph-canvas]');

  function slot(name) { return overlay.querySelector('[data-expand-slot="' + name + '"]'); }
  function anchor(name) { return document.querySelector('[data-anchor-for="' + name + '"]'); }
  function section(name) { return document.querySelector('[data-movable="' + name + '"]'); }

  function move(name, into) {
    var moving = section(name);
    if (moving && into) { into.appendChild(moving); }
  }

  function refit() {
    // The frame changed size, so whatever is holding the zoom has to fit to it again. The same
    // event the view switch sends, for the same reason.
    if (canvas) { canvas.dispatchEvent(new CustomEvent('metric:viewchanged')); }
  }

  function open() {
    overlay.hidden = false;
    document.body.classList.add('is-expanded');
    // Whichever side panel this stage has. The intake stage carries the declaration editor, every
    // stage after it carries the scenario space, and no stage carries both -- the declaration is
    // editable only where nothing has been built from it yet.
    move('declaration', slot('space'));
    move('space', slot('space'));
    move('graph', slot('graph'));

    // Folded away, the graph would open to an empty pane. Whatever state the section was left in
    // on the stage is not the state that makes sense here.
    var folded = overlay.querySelector('[data-graph-section]');
    if (folded) { folded.open = true; }

    if (note) {
      var beside = slot('space').firstElementChild;
      note.textContent = !beside
        ? 'Hover to follow one thread · drag to pan · Escape to close'
        : beside.dataset.movable === 'declaration'
          ? 'Select a row to light it · edit and save · Escape to close'
          : 'Open a scenario to light its route · Escape to close';
    }
    // The button rides inside the graph section, so it comes along. Hidden rather than left
    // showing, because "open full screen" on a screen that is already full reads as a control
    // that does nothing.
    opener.hidden = true;
    refit();
    if (closer) { closer.focus(); }
  }

  function close() {
    // Back where each came from, in the order the page had them. The anchors exist because a
    // section put back at the end of the page would be in the wrong place and would stay there.
    ['graph', 'space', 'declaration'].forEach(function (name) {
      var moved = section(name), home = anchor(name);
      if (moved && home && home.parentNode) {
        home.parentNode.insertBefore(moved, home.nextSibling);
      }
    });
    overlay.hidden = true;
    opener.hidden = false;
    document.body.classList.remove('is-expanded');
    refit();
    opener.focus();
  }

  opener.addEventListener('click', function (event) {
    // The button sits inside the section's <summary>, so a click on it is also a click on the
    // disclosure. Without this, expanding also folds the section away underneath -- and the fold
    // is what is showing again the moment the overlay closes.
    event.preventDefault();
    event.stopPropagation();
    open();
  });
  if (closer) { closer.addEventListener('click', close); }

  // Escape closes the overlay, and only the overlay. The route highlighting also listens for it
  // to clear open cards, which is the right thing on the stage and the wrong thing here -- one
  // key press should not both drop the selection and put the window away.
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || overlay.hidden) { return; }
    event.stopPropagation();
    close();
  }, true);
})();
