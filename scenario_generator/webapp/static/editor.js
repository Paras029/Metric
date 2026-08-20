// Editing the declaration beside the drawing.
//
// The workbook is the record and stays the record. What this removes is the round trip to change
// one cell of it -- download, find the row, edit, save, upload -- because the judgement being made
// in that loop is made by looking at the graph, which is on screen the whole time.
//
// Everything here is an enhancement over forms that already work. With scripting off each row
// posts on its own and the page reloads, which is what the rest of this interface does. With it,
// three things become possible that a plain form cannot do:
//
//   **Selecting a row lights it in the graph.** That answers "which part of the agent is this"
//   without anybody tracing it, and the answer differs by kind: a decision is a box, a state is
//   usually an *arrow* (only a terminal state has a box of its own), a capability is every
//   decision tagged with it, and a tool is the decisions of the capability it belongs to.
//
//   **Edits are staged.** Correcting a branch usually means touching a decision and the states
//   around it, and saving each separately leaves the declaration briefly incoherent, with the
//   audit complaining about a half-finished edit.
//
//   **A preview before saving.** The server applies the staged edits to a copy of the workbook and
//   returns the drawing it would produce, so a new decision can be seen attaching to the graph
//   before anything is written.
(function () {
  var editor = document.querySelector('[data-editor]');
  if (!editor) { return; }

  var canvas = document.querySelector('[data-graph-canvas]');
  var stateLine = editor.querySelector('[data-editor-state]');
  var reportBox = editor.querySelector('[data-editor-report]');
  var previewButton = editor.querySelector('[data-editor-preview]');
  var saveButton = editor.querySelector('[data-editor-save]');
  var refreshButton = editor.querySelector('[data-editor-refresh]');

  // Keyed by kind and key, so a row edited, collapsed and edited again is one staged edit rather
  // than two -- the second of which would carry only the fields touched the second time.
  var staged = {};
  var lit = [];
  var previewing = false;

  // Which row is selected, tracked rather than read back off the DOM. Opening one row closes the
  // row that was open, and *that* fires its own toggle -- asynchronously, so it arrives after the
  // new row has lit its part of the graph and darkens it again. The symptom is a selection that
  // works once and never afterwards, which gets diagnosed as "the highlighting is flaky".
  var selected = null;

  function drawings() {
    return canvas ? Array.prototype.slice.call(canvas.querySelectorAll('svg')) : [];
  }

  function quoted(value) {
    return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function darken() {
    // The flag the graph's own hover reads. A selected row has to survive the pointer moving over
    // the drawing to look at what it lit; without this, the hover handler takes over on the way
    // there and the selection is gone before it is read.
    document.body.classList.remove('is-holding');
    drawings().forEach(function (one) { one.classList.remove('graph--focused'); });
    lit.forEach(function (el) {
      el.classList.remove('is-lit');
      el.classList.remove('is-lit--source');
    });
    lit = [];
  }

  function light(selector) {
    drawings().forEach(function (one) {
      Array.prototype.forEach.call(one.querySelectorAll(selector), function (el) {
        el.classList.add('is-lit');
        el.classList.add('is-lit--source');
        lit.push(el);
      });
    });
  }

  function say(text) { if (stateLine) { stateLine.textContent = text || ''; } }

  function highlightsFor(kind, key) {
    var all = window.METRIC_HIGHLIGHTS || {};
    return (all[kind] || {})[key] || null;
  }

  function show(kind, key) {
    darken();
    var found = highlightsFor(kind, key);
    if (!found) { return; }
    if (!found.nodes.length && !found.edges.length) {
      // Said rather than left silent. A selection that lights nothing reads as the highlighting
      // being broken, when for a persona it is the correct answer.
      say(kind === 'persona'
        ? 'Every route is walked by every persona, so there is nothing to light.'
        : key + ' is not connected to the graph yet.');
      return;
    }
    document.body.classList.add('is-holding');
    drawings().forEach(function (one) { one.classList.add('graph--focused'); });
    found.nodes.forEach(function (id) { light('[data-node="' + quoted(id) + '"]'); });
    found.edges.forEach(function (edge) {
      light('[data-source="' + quoted(edge[0]) + '"][data-target="' + quoted(edge[1]) +
            '"][data-outcome="' + quoted(edge[2]) + '"]');
    });
  }

  function countStaged() { return Object.keys(staged).length; }

  function announce() {
    var count = countStaged();
    if (previewButton) { previewButton.hidden = !count; }
    if (saveButton) { saveButton.hidden = !count; }
    if (!count) {
      say(previewing ? 'Showing an unsaved change. Refresh to put it back.' : '');
      return;
    }
    say(count === 1 ? '1 unsaved change' : count + ' unsaved changes');
  }

  // --------------------------------------------------------------------------- tabs
  var tabs = Array.prototype.slice.call(editor.querySelectorAll('[data-editor-tab]'));
  var panels = Array.prototype.slice.call(editor.querySelectorAll('[data-editor-panel]'));

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var wanted = tab.dataset.editorTab;
      tabs.forEach(function (other) {
        other.setAttribute('aria-selected', String(other === tab));
      });
      panels.forEach(function (panel) {
        panel.hidden = panel.dataset.editorPanel !== wanted;
      });
      // Switching what is being edited drops the highlight. A decision left lit while the tools
      // list is showing says that tool is that decision.
      selected = null;
      darken();
    });
  });

  // --------------------------------------------------------------------------- selection
  //
  // The row's own disclosure is the selector. Nothing else on the page is one, so there is no
  // second control that can disagree with what is open.
  editor.addEventListener('toggle', function (event) {
    var body = event.target;
    if (!body.classList || !body.classList.contains('erow__body')) { return; }
    var row = body.closest('[data-row-kind]');
    if (!row) { return; }

    if (body.open) {
      // One at a time. Several open rows would light several parts of the graph with no way to
      // tell which is which. The scenario list opens several deliberately, because there the
      // comparison between two routes is the point; here the question is about one row.
      Array.prototype.forEach.call(
        editor.querySelectorAll('.erow__body[open]'), function (other) {
          if (other !== body) { other.open = false; }
        });
      selected = body;
      show(row.dataset.rowKind, row.dataset.rowKey);
    } else if (body === selected) {
      selected = null;
      darken();
    }
  }, true);

  // --------------------------------------------------------------- building a capability
  //
  // A capability is built from its decisions, and everything else about it follows. That order
  // was the thing that did not work before: membership is recorded on the *decisions*, so the
  // capability's own row could not change it, while the boundary controls on that row were
  // computed from it. A wrong grouping could be seen and not corrected, and every shortlist
  // derived from it was wrong in the same way.
  //
  // So: tick the decisions, see the states they fold in, pick the boundary from those. The middle
  // step is pure set arithmetic over adjacency the page already carries -- which states offer a
  // decision, which states its outcomes land on -- so it answers as fast as the ticking. What
  // counts as an *exit* is policy and stays in one language: "Show it in the graph" asks the tool
  // for that rather than working it out again here.
  function adjacency() { return window.METRIC_GRAPH || { decisions: {}, states: {} }; }

  // Two halves, not one set. A capability is entered at a state that *offers* one of its
  // decisions and left at a state one of them *routes to*, and those are different lists -- so
  // offering the union to both put "the chat opens" among the ways a block can end, under a line
  // saying these are the states its decisions route to. The union is still what the block folds
  // in, which is the count worth reporting.
  function foldedIn(row) {
    var graph = adjacency();
    var into = {}, outOf = {};
    Array.prototype.forEach.call(
      row.querySelectorAll('[data-owns-decision]:checked'), function (box) {
        var decision = graph.decisions[box.value];
        if (!decision) { return; }
        decision.offered_by.forEach(function (id) { into[id] = true; });
        decision.lands_on.forEach(function (id) { outOf[id] = true; });
      });
    var all = {};
    Object.keys(into).forEach(function (id) { all[id] = true; });
    Object.keys(outOf).forEach(function (id) { all[id] = true; });
    return {
      entry_states: Object.keys(into).sort(),
      exit_states: Object.keys(outOf).sort(),
      all: Object.keys(all).sort(),
    };
  }

  function stateLabel(id) {
    var state = adjacency().states[id];
    return state ? state.label : id;
  }

  function isTerminal(id) {
    var state = adjacency().states[id];
    return !!(state && state.terminal);
  }

  // The boundary lists, redrawn over the states the ticked decisions fold in. Anything already
  // ticked is kept even where it falls outside that set -- a span may legitimately begin or end
  // anywhere, and silently dropping a boundary because the membership changed would be an edit
  // nobody made.
  function redrawBoundary(row, picker, folded) {
    var name = picker.dataset.boundary;
    var list = picker.querySelector('[data-boundary-shortlist]');
    if (!list) { return; }

    var ticked = {};
    Array.prototype.forEach.call(
      picker.querySelectorAll('input:checked'), function (box) { ticked[box.value] = true; });

    var offer = folded.slice();
    Object.keys(ticked).forEach(function (id) {
      if (offer.indexOf(id) < 0) { offer.push(id); }
    });

    list.textContent = '';
    offer.forEach(function (id) {
      var item = document.createElement('li');
      var label = document.createElement('label');
      label.className = 'check';

      var box = document.createElement('input');
      box.type = 'checkbox';
      box.name = name;
      box.value = id;
      box.checked = !!ticked[id];
      box.setAttribute('data-field', name);
      box.setAttribute('data-multiple', '');

      var mono = document.createElement('span');
      mono.className = 'mono';
      mono.textContent = id;

      label.appendChild(box);
      label.appendChild(mono);
      label.appendChild(document.createTextNode(' ' + stateLabel(id)));
      if (isTerminal(id)) {
        var ends = document.createElement('span');
        ends.className = 'mark';
        ends.textContent = 'ends';
        label.appendChild(ends);
      }
      item.appendChild(label);
      list.appendChild(item);
    });

    // On what basis this list was drawn, in the same breath as drawing it. Said separately from
    // the server's own rendering because it is a statement about the membership, and the
    // membership is being changed on this page: rendered once at page build it would go on naming
    // whichever decisions were ticked at the time.
    var why = picker.querySelector('[data-boundary-why]');
    if (why) {
      var owned = Array.prototype.map.call(
        row.querySelectorAll('[data-owns-decision]:checked'), function (box) { return box.value; });
      why.textContent = !owned.length
        ? 'Tick the decisions above first — the boundary is picked from the states they fold in.'
        : (name === 'entry_states'
            ? 'States that offer ' + owned.join(', ') + '.'
            : 'States that ' + owned.join(', ') + ' route to.');
    }

    // And take out of the "every state" list whatever is now offered above it, so nothing is
    // shown twice with two boxes that disagree.
    Array.prototype.forEach.call(
      picker.querySelectorAll('.span__list--long input'), function (box) {
        var item = box.closest('li');
        if (item) { item.hidden = offer.indexOf(box.value) >= 0; }
      });
  }

  function rebuild(row) {
    var folded = foldedIn(row);
    var says = row.querySelector('[data-folds-in]');
    if (says) {
      says.textContent = folded.all.length
        ? ('Those decisions fold in ' + folded.all.length + ' state(s): ' +
           folded.all.join(', ') + '. The boundary below is picked from them.')
        : 'No decisions ticked, so this capability folds in nothing and is not walked.';
    }
    Array.prototype.forEach.call(row.querySelectorAll('[data-boundary]'), function (picker) {
      redrawBoundary(row, picker, folded[picker.dataset.boundary] || []);
    });
  }

  Array.prototype.forEach.call(editor.querySelectorAll('[data-row-kind="capability"]'),
                               function (row) { rebuild(row); });

  editor.addEventListener('change', function (event) {
    if (!event.target.hasAttribute || !event.target.hasAttribute('data-owns-decision')) { return; }
    var row = event.target.closest('[data-row-kind="capability"]');
    if (row) { rebuild(row); }
  });

  // --------------------------------------------------------------------------- staging
  function collect(form) {
    var fields = {};
    Array.prototype.forEach.call(form.querySelectorAll('[data-field]'), function (input) {
      var name = input.dataset.field;
      if (input.hasAttribute('data-multiple')) {
        // A group of checkboxes standing for one list field -- a capability's entry states, say.
        // Collected as an array, and an empty one where nothing is ticked, because clearing a
        // span has to be as available as drawing one: a field that only ever gains values is one
        // a mistake cannot be taken out of.
        if (!Array.isArray(fields[name])) { fields[name] = []; }
        if (input.checked) { fields[name].push(input.value); }
        return;
      }
      fields[name] = input.type === 'checkbox' ? input.checked : input.value;
    });
    return fields;
  }

  function stage(row, form) {
    staged[row.dataset.rowKind + ' ' + row.dataset.rowKey] = {
      kind: row.dataset.rowKind,
      key: row.dataset.rowKey,
      action: 'upsert',
      fields: collect(form),
    };
    row.classList.add('erow--dirty');
    announce();
  }

  editor.addEventListener('input', function (event) {
    var form = event.target.closest && event.target.closest('.erow__form');
    var row = form && form.closest('[data-row-kind]');
    if (row) { stage(row, form); }
  });
  editor.addEventListener('change', function (event) {
    // Checkboxes and selects fire change rather than input in older engines, and a flag that
    // silently fails to stage is the worst of the possible bugs here: it looks saved.
    var form = event.target.closest && event.target.closest('.erow__form');
    var row = form && form.closest('[data-row-kind]');
    if (row) { stage(row, form); }
  });

  // Remove is staged like everything else, so a deletion can be previewed. Seeing what stops
  // being reachable before the row goes is most of what makes a deletion safe to make.
  editor.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('button[value="delete"]');
    if (!button) { return; }
    var row = button.closest('[data-row-kind]');
    if (!row) { return; }
    event.preventDefault();
    var id = row.dataset.rowKind + ' ' + row.dataset.rowKey;
    if (staged[id] && staged[id].action === 'delete') {
      delete staged[id];
      row.classList.remove('erow--removing');
    } else {
      staged[id] = { kind: row.dataset.rowKind, key: row.dataset.rowKey, action: 'delete' };
      row.classList.add('erow--removing');
      row.classList.remove('erow--dirty');
    }
    announce();
  });

  // Adding one writes it immediately rather than staging it. A staged addition is an intention
  // with nothing on screen to show for it: the row does not exist yet, so there is nothing to
  // open, nothing to type into, and the only evidence is a counter -- which reads exactly like the
  // button having done nothing. An empty row costs nothing to create and is the thing being asked
  // for, so it is created, and the page comes back with it open and ready to fill in.
  Array.prototype.forEach.call(editor.querySelectorAll('.erow__new'), function (form) {
    form.addEventListener('submit', function (event) {
      var input = form.querySelector('[name="key"]');
      var kind = form.querySelector('[name="kind"]').value;
      var key = input && input.value.trim();
      if (!key) { return; }
      event.preventDefault();
      if (countStaged() && !window.confirm(
        'Adding ' + key + ' saves everything, including ' + countStaged() +
        ' unsaved change(s). Continue?')) { return; }

      say('Adding ' + key + '…');
      staged[kind + ' ' + key] = { kind: kind, key: key, action: 'upsert', fields: {} };
      post('/stage/intake/declaration/save', { edits: edits() }).then(function (answer) {
        if (answer.error) { report(answer); say('That could not be added'); return; }
        reopen(kind, key);
      }).catch(function () { say('Could not reach the tool'); });
    });
  });

  // --------------------------------------------------------------------------- talking to it
  function edits() {
    return Object.keys(staged).map(function (id) { return staged[id]; });
  }

  function post(where, body) {
    return fetch(where, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (answer) { return answer.json(); });
  }

  function swapDrawings(answer) {
    if (!canvas) { return; }
    var blocks = canvas.querySelector('[data-graph-view="blocks"]');
    var detail = canvas.querySelector('[data-graph-view="detail"]');
    if (detail) { detail.innerHTML = answer.graph_svg || ''; }
    else { canvas.innerHTML = answer.graph_svg || ''; }
    if (blocks) { blocks.innerHTML = answer.blocks_svg || ''; }
    if (answer.highlights) { window.METRIC_HIGHLIGHTS = answer.highlights; }
    if (answer.graph) { window.METRIC_GRAPH = answer.graph; }
    // A span drawn where there were none makes a collapsed view possible for the first time, and
    // the control that offers it has to notice.
    canvas.dispatchEvent(new CustomEvent('metric:drawingschanged'));
    // The counts under the drawing, or the page disagrees with itself: a graph redrawn with a new
    // branch in it, under a line still reporting the old number of outcomes.
    Object.keys(answer.facts || {}).forEach(function (name) {
      var cell = document.querySelector('[data-fact="' + name + '"]');
      if (cell && typeof answer.facts[name] !== 'object') {
        cell.textContent = answer.facts[name];
      }
    });
    canvas.dispatchEvent(new CustomEvent('metric:viewchanged'));
  }

  function report(answer) {
    if (!reportBox) { return; }
    var lines = [];
    (answer.report && answer.report.refused ? answer.report.refused : []).forEach(function (line) {
      lines.push(['bad', line]);
    });
    (answer.report && answer.report.dangling ? answer.report.dangling : []).forEach(
      function (line) { lines.push(['warn', line]); });
    if (answer.error) { lines.push(['bad', answer.error]); }
    reportBox.textContent = '';
    reportBox.hidden = !lines.length;
    lines.forEach(function (pair) {
      var line = document.createElement('p');
      line.className = 'aside aside--' + pair[0];
      line.textContent = pair[1];
      reportBox.appendChild(line);
    });
  }

  if (previewButton) {
    previewButton.addEventListener('click', function () {
      say('Working it out…');
      post('/stage/intake/declaration/preview', { edits: edits() }).then(function (answer) {
        report(answer);
        if (answer.error) { say('That could not be drawn'); return; }
        swapDrawings(answer);
        previewing = true;
        var count = countStaged();
        say(count + (count === 1 ? ' unsaved change' : ' unsaved changes') + ', shown above');
      }).catch(function () { say('Could not reach the tool'); });
    });
  }

  if (saveButton) {
    saveButton.addEventListener('click', function () {
      say('Saving…');
      post('/stage/intake/declaration/save', { edits: edits() }).then(function (answer) {
        if (answer.error) { report(answer); say('That could not be saved'); return; }
        reopen();
      }).catch(function () { say('Could not reach the tool'); });
    });
  }

  if (refreshButton) {
    refreshButton.addEventListener('click', function () {
      if (countStaged() && !window.confirm(
        'Refreshing drops ' + countStaged() + ' unsaved change(s). Continue?')) { return; }
      reopen();
    });
  }

  // Reloaded rather than patched. Every row's fields, the count on every tab, the open questions
  // and which later stages the save put out of date all change together, and rebuilding that from
  // JSON is a second renderer that will disagree with the first one within a week.
  //
  // What the reload must not do is put the reader back where they did not ask to be. The editor
  // is used from the expanded view, where the rows and the drawing are side by side -- and a save
  // that dropped back to the stage page took away the arrangement the work was being done in,
  // every single time. So what is on screen is written down first and put back afterwards: the
  // overlay, the tab, and the row to open.
  function reopen(kind, key) {
    var open = editor.querySelector('.erow__body[open]');
    var openRow = open && open.closest('[data-row-kind]');
    var tab = editor.querySelector('[data-editor-tab][aria-selected="true"]');
    try {
      window.sessionStorage.setItem('metric:editor', JSON.stringify({
        expanded: !document.querySelector('[data-expand]').hidden,
        tab: kind || (tab && tab.dataset.editorTab) || '',
        row: key || (openRow && openRow.dataset.rowKey) || '',
      }));
    } catch (error) { /* private browsing, or storage full. The reload still works. */ }
    window.location.reload();
  }

  // And put back, once the page it belongs to has been rebuilt.
  (function restore() {
    var saved;
    try {
      saved = JSON.parse(window.sessionStorage.getItem('metric:editor') || 'null');
      window.sessionStorage.removeItem('metric:editor');
    } catch (error) { return; }
    if (!saved) { return; }

    if (saved.expanded) {
      var opener = document.querySelector('[data-expand-open]');
      if (opener) { opener.click(); }
    }
    if (saved.tab) {
      var tab = editor.querySelector('[data-editor-tab="' + quoted(saved.tab) + '"]');
      if (tab) { tab.click(); }
    }
    if (saved.row) {
      var row = editor.querySelector('[data-row-key="' + quoted(saved.row) + '"] .erow__body');
      if (row) {
        row.open = true;
        if (row.scrollIntoView) { row.scrollIntoView({ block: 'center' }); }
      }
    }
  })();

  // Without scripting, Remove posts straight through and cannot be taken back, so the form asks
  // first. With scripting it is staged and reversible, so it does not -- a confirmation on
  // something you can undo by clicking again is noise that teaches people to dismiss them.
  Array.prototype.forEach.call(document.querySelectorAll('[data-confirm]'), function (button) {
    button.removeAttribute('data-confirm');
  });

  announce();
})();
