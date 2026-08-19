"""Three things that made a real declared graph hard to read, and what the picture does instead.

**A box was sometimes a decision and sometimes an ending, drawn identically.** The caption above
the picture had been claiming "rounded boxes the ways an interaction can end" for some time, and
every box carried the same corner radius -- so the page named a distinction the drawing never
made, and a reader could not tell a branch point from a terminus without hovering over it. The
intake was never ambiguous about which was which; the SVG was.

**The same ending was drawn once per route into it.** A drafted intake writes one terminal state
per decision outcome, so "escalated to a case handler" arrives as four states with four ids. Four
boxes is four endings, which is not what the agent does, and it is what fills the right-hand
columns with boxes that are all the same box.

**And every line crossed every other.** The question a reader has in front of a graph like that is
never "what is the whole shape" -- it is "where does *this* come from and where does it go", which
until now they had to answer by tracing with a finger.

What is pinned here is the data behind all three. The hover itself is browser behaviour and is
checked by driving one; what these tests hold is that the SVG carries what a hover needs -- every
arrow saying which two boxes it joins -- because that is the part that silently stops being true.
"""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from scenario_generator.core.models import Capability, Decision, IntakeData, Persona, State, Tool
from scenario_generator import webapp
from scenario_generator.core import DecisionGraph, enumerate_paths, instantiate_all
from scenario_generator.core.intake import read_intake, write_template
from scenario_generator.io import write_space_metadata
from scenario_generator.webapp.app import create_app
from scenario_generator.webapp.graphview import (BOX_HEIGHT, DECISION, TERMINAL, build_layout,
                                                 graph_summary, render_svg, routes)

_PERSONA = Persona("P1", "Cardmember", [], True)


def _intake(states, decisions=None):
    return IntakeData(
        use_case={"Use case name": "Servicing", "Business objective": "Serve"},
        personas=[_PERSONA],
        capabilities=[Capability("CAP-01", "Identity", "Gating")],
        decisions=decisions or [
            Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass", "Fail"]),
            Decision("DEC-02", "What is asked", "CAP-01", "", ["Dispute", "Unclear"]),
        ],
        states=states,
        tools=[Tool("Identity service", "CAP-01", True)],
    )


_SPLIT_ENDINGS = _intake([
    State("S-00", "Start", "The chat opens", ["DEC-01"], False),
    State("S-01", "DEC-01=Pass", "Verified", ["DEC-02"], False),
    # The same ending, declared once per route into it -- which is what a drafted intake produces.
    State("S-02", "DEC-01=Fail", "Escalated to a case handler", [], True, "Escalation"),
    State("S-03", "DEC-02=Dispute", "Dispute filed", [], True, "Happy path"),
    State("S-04", "DEC-02=Unclear", "Escalated to a case handler", [], True, "Escalation"),
])


class TestABoxSaysWhatKindOfThingItIs(unittest.TestCase):
    """Shape rather than colour: it survives printing, and it does not spend the one colour idea
    the interface has."""

    def test_a_decision_is_square_and_an_ending_is_a_stadium(self):
        svg = render_svg(_SPLIT_ENDINGS)
        corners = re.findall(r'<rect x="[\d.]+" y="[\d.]+" width="190" height="62" rx="([\d.]+)"',
                             svg)
        self.assertIn("3", corners, "no square-cornered box: a decision is drawn like an ending")
        self.assertIn(f"{BOX_HEIGHT / 2}", corners, "no stadium: an ending is drawn like a branch")

    def test_the_two_shapes_are_actually_different(self):
        """The caption promised this distinction long before the drawing made it."""
        from scenario_generator.webapp.graphview import _corner

        self.assertNotEqual(_corner(DECISION), _corner(TERMINAL))

    def test_the_page_and_the_picture_agree_about_the_shapes(self):
        """A legend describing a distinction the drawing does not make is worse than no legend."""
        from pathlib import Path

        from scenario_generator.webapp import app as webapp

        template = (Path(webapp.__file__).parent / "templates" / "stage.html").read_text()
        self.assertIn("rounded boxes the endings", template)


class TestOneEndingIsOneBox(unittest.TestCase):
    def test_states_describing_the_same_ending_are_drawn_once(self):
        layout = build_layout(_SPLIT_ENDINGS)
        terminals = [n for n in layout.nodes.values() if n.kind == TERMINAL]
        self.assertEqual(len(terminals), 2, "three declared endings, two distinct ones")

    def test_every_route_into_it_still_arrives(self):
        """Merging the boxes must not lose an arrow -- that would hide a route rather than tidy
        the picture."""
        layout = build_layout(_SPLIT_ENDINGS)
        escalation = next(n for n in layout.nodes.values()
                          if n.caption == "Escalated to a case handler")
        arriving = [e for e in layout.edges if e.target == escalation.id]
        self.assertEqual(sorted(e.source for e in arriving), ["DEC-01", "DEC-02"])

    def test_the_box_names_every_id_it_stands_for(self):
        layout = build_layout(_SPLIT_ENDINGS)
        escalation = next(n for n in layout.nodes.values()
                          if n.caption == "Escalated to a case handler")
        self.assertEqual(escalation.merged_ids, ("S-02", "S-04"))
        self.assertIn("S-02, S-04", escalation.detail)
        self.assertIn("describe the same ending", escalation.detail)

    def test_it_is_reported_as_a_finding_rather_than_quietly_tidied(self):
        """An intake that says the same thing three times is a declaration to tidy, and the
        picture hiding that would be the picture covering for it."""
        self.assertEqual(graph_summary(_SPLIT_ENDINGS)["duplicate_endings"],
                         [("S-02", "S-04")])

    def test_endings_that_differ_by_a_word_are_left_alone(self):
        """Two endings that differ may well be two endings, and merging them would lie about the
        agent in the direction that hides a route."""
        nearly = _intake([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Escalated to a case handler", [], True, "Escalation"),
            State("S-02", "DEC-01=Fail", "Escalated to a fraud handler", [], True, "Escalation"),
        ])
        self.assertEqual(graph_summary(nearly)["duplicate_endings"], [])

    def test_the_same_words_under_a_different_outcome_type_are_two_endings(self):
        differing = _intake([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Handed to a person", [], True, "Escalation"),
            State("S-02", "DEC-01=Fail", "Handed to a person", [], True, "Termination"),
        ])
        self.assertEqual(graph_summary(differing)["duplicate_endings"], [])

    def test_endings_with_no_description_are_never_merged_together(self):
        """Grouping every blank one would invent a convergence out of an omission."""
        blank = _intake([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "", [], True, "Happy path"),
            State("S-02", "DEC-01=Fail", "", [], True, "Termination"),
        ])
        layout = build_layout(blank)
        self.assertEqual(len([n for n in layout.nodes.values() if n.kind == TERMINAL]), 2)
        self.assertEqual(graph_summary(blank)["duplicate_endings"], [])

    def test_a_merged_ending_is_not_reported_as_unreachable(self):
        """Its id is no longer a node of its own, which is exactly how a reachability check that
        did not know about merging would decide nothing leads to it."""
        self.assertEqual(graph_summary(_SPLIT_ENDINGS)["unreachable"], [])


class TestThePictureAndTheWalkerAgree(unittest.TestCase):
    """The one wiring bug in here that was not about readability at all.

    The picture used to parse `Reached Via` itself, comparing the whole cell for exact equality
    after stripping spaces, while the walk parses it with a regex that finds every `DEC-nn=Outcome`
    pair in the cell and normalises the outcome. Two perfectly ordinary declarations were therefore
    walked and not drawn, and a route that is enumerated, written up and issued but invisible on
    the one screen anybody checks the declaration on is the worst direction for a drawing to be
    wrong in: nobody questions a branch they cannot see.

    These compare the two directly rather than asserting on either alone, because what matters is
    not what either does -- it is that they cannot drift apart again.
    """

    def _both(self, states):
        from scenario_generator.core.graph import DecisionGraph

        intake = _intake(states)
        graph = DecisionGraph(intake.decisions, intake.states)
        walked = {(d.id, v) for d in intake.decisions for v in d.variants
                  if not graph.successor(d.id, v).startswith("OUT:")}
        drawn = {(e.source, e.outcome) for e in build_layout(intake).edges if e.outcome}
        return walked, drawn

    def test_a_state_two_outcomes_converge_on_is_drawn_from_both(self):
        """The shape the drafting prompt now explicitly asks for: `DEC-01=Fail, DEC-02=Unclear`."""
        walked, drawn = self._both([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Verified", ["DEC-02"], False),
            State("S-99", "DEC-01=Fail, DEC-02=Unclear", "Handed to a person", [], True,
                  "Escalation"),
            State("S-02", "DEC-02=Dispute", "Dispute filed", [], True, "Happy path"),
        ])
        self.assertEqual(walked - drawn, set())
        self.assertIn(("DEC-01", "Fail"), drawn)
        self.assertIn(("DEC-02", "Unclear"), drawn)

    def test_an_outcome_carrying_its_retry_bound_is_drawn(self):
        walked, drawn = self._both([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Verified", ["DEC-02"], False),
            State("S-03", "DEC-01=Fail (attempt<3)", "Locked out", [], True, "Termination"),
            State("S-02", "DEC-02=Dispute", "Dispute filed", [], True, "Happy path"),
        ])
        self.assertEqual(walked - drawn, set())

    def test_a_start_state_worded_any_way_the_walk_accepts_opens_the_picture(self):
        """The walk takes "Inbound call" as an opening; the picture took only the word "Start"."""
        intake = _intake([
            State("S-00", "Inbound call", "The call connects", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Verified", [], True, "Happy path"),
            State("S-02", "DEC-01=Fail", "Locked out", [], True, "Termination"),
        ])
        layout = build_layout(intake)
        opening = [e for e in layout.edges if e.source == "__start__"]
        self.assertEqual([e.target for e in opening], ["DEC-01"])

    def test_the_ordinary_case_still_agrees(self):
        walked, drawn = self._both(_SPLIT_ENDINGS.states)
        self.assertEqual(walked - drawn, set())

    def test_the_picture_does_not_parse_reached_via_itself_any_more(self):
        """One authority on what leads where. A second reader of the same cell is what produced
        the disagreement, and re-adding one would produce it again."""
        from pathlib import Path

        from scenario_generator.webapp import graphview

        source = Path(graphview.__file__).read_text(encoding="utf-8")
        self.assertNotIn("reached_via.strip().lower()", source)
        self.assertIn("DecisionGraph", source)


class TestTheSvgCarriesWhatAHoverNeeds(unittest.TestCase):
    """The highlighting is browser behaviour; these hold the data it reads."""

    def test_every_arrow_says_which_two_boxes_it_joins(self):
        svg = render_svg(_SPLIT_ENDINGS)
        arrows = re.findall(r'<g class="graph__edge[^"]*" data-source="([^"]+)" '
                            r'data-target="([^"]+)"', svg)
        self.assertEqual(len(arrows), len(build_layout(_SPLIT_ENDINGS).edges))

    def test_an_arrows_label_carries_the_same_pair_so_the_two_light_together(self):
        """They are two elements standing for one thing, and lighting one without the other reads
        as a rendering fault."""
        svg = render_svg(_SPLIT_ENDINGS)
        labels = re.findall(r'<g class="graph__label[^"]*"[^>]*data-source="([^"]+)" '
                            r'data-target="([^"]+)"', svg)
        arrows = re.findall(r'<g class="graph__edge[^"]*" data-source="([^"]+)" '
                            r'data-target="([^"]+)"', svg)
        self.assertEqual(sorted(labels), sorted(arrows))

    def test_every_box_is_addressable_by_id(self):
        svg = render_svg(_SPLIT_ENDINGS)
        drawn = set(re.findall(r'data-node="([^"]+)"', svg))
        self.assertEqual(drawn, set(build_layout(_SPLIT_ENDINGS).nodes))

    def test_every_endpoint_an_arrow_names_is_a_box_that_exists(self):
        """A hover looks a box up by the id on the arrow; one that resolves to nothing lights
        half a thread and says nothing about it."""
        layout = build_layout(_SPLIT_ENDINGS)
        for edge in layout.edges:
            self.assertIn(edge.source, layout.nodes)
            self.assertIn(edge.target, layout.nodes)

    def test_the_reading_controls_are_one_file_shared_with_the_downloadable_copy(self):
        """The drawing leaves the tool -- into a validation report, into a mail to the model owner
        -- and a copy that has lost its zoom and its highlighting is a screenshot with extra steps.
        Inlining the same file in both places is what keeps the two from drifting; a second copy of
        the script is how they drift."""
        from pathlib import Path as _Path
        script = (_Path(webapp.__file__).parent / "static" / "graph.js").read_text()
        for behaviour in ("data-route", "data-graph-clear", "data-zoom", "graph--focused"):
            self.assertIn(behaviour, script)

        page = (_Path(webapp.__file__).parent / "templates"
                / "graph_standalone.html").read_text()
        self.assertIn("{{ script | safe }}", page)
        self.assertIn("{{ stylesheet }}", page)
        # Nothing fetched: a page that asks a server for its stylesheet is a page that renders as
        # unstyled markup the moment it is opened from a mail attachment.
        self.assertNotIn("url_for(", page)

    def test_the_graph_still_draws_with_no_scripting_at_all(self):
        """Nothing here is required for the picture to work: the classes the hover adds are added
        by a browser, and the SVG carries its own tooltips."""
        svg = render_svg(_SPLIT_ENDINGS)
        self.assertNotIn("graph--focused", svg)
        self.assertNotIn("is-lit", svg)
        self.assertIn("<title>", svg)


class TestWhereAScenarioRunsInThePicture(unittest.TestCase):
    """A scenario is a route through this graph, and until now the page never said which one.

    The card had the words and the picture beside it had forty boxes, with nothing joining the
    two -- so "escalates after the second failed attempt" was matched to a path by eye, or not at
    all. What is pinned here is the mapping that lets a card light its own route: that it names
    arrows the drawing actually contains, that it names the *right* arrow where a decision has two
    outcomes reaching the same place, and that an ending drawn as one box is pointed at by the box
    that was drawn rather than by an id that was not.
    """

    def _scenarios(self, intake):
        graph = DecisionGraph(intake.decisions, intake.states)
        walked, augmented = enumerate_paths(graph)
        return graph, instantiate_all(walked, augmented, graph, intake.personas, intake.tools)

    def test_a_route_runs_from_the_start_box_to_an_ending(self):
        """The whole route, not the middle of it. A highlight that begins at the second decision
        does not answer "where does this scenario come in", which is half the question."""
        layout = build_layout(_SPLIT_ENDINGS)
        _, scenarios = self._scenarios(_SPLIT_ENDINGS)
        for scenario, route in routes(_SPLIT_ENDINGS, scenarios).items():
            edges = route["edges"]
            self.assertEqual(edges[0][0], "__start__", scenario)
            self.assertEqual(layout.nodes[edges[-1][1]].kind, TERMINAL, scenario)
            # Each arrow leaves where the one before it landed, so the lit path is continuous.
            for before, after in zip(edges, edges[1:]):
                self.assertEqual(before[1], after[0], scenario)

    def test_every_arrow_a_route_names_is_one_the_drawing_contains(self):
        """A route walking an arrow the picture does not have is the walk and the drawing
        disagreeing, and lighting nothing is how that disagreement would go unnoticed."""
        _, scenarios = self._scenarios(_SPLIT_ENDINGS)
        drawn = {(e.source, e.target, e.outcome) for e in build_layout(_SPLIT_ENDINGS).edges}
        for route in routes(_SPLIT_ENDINGS, scenarios).values():
            for edge in route["edges"]:
                self.assertIn(tuple(edge), drawn)

    def test_an_ending_drawn_as_one_box_is_pointed_at_by_that_box(self):
        """S-02 and S-04 declare the same ending and are drawn once. A route ending at S-04 has to
        light the box that stands for it, or it lights nothing at all."""
        _, scenarios = self._scenarios(_SPLIT_ENDINGS)
        walked = routes(_SPLIT_ENDINGS, scenarios)
        drawn = set(build_layout(_SPLIT_ENDINGS).nodes)
        for route in walked.values():
            for node_id in route["nodes"]:
                self.assertIn(node_id, drawn)
        # And the merged ending is genuinely reached by something, so the case is exercised.
        self.assertTrue(any("S-02" in route["nodes"] for route in walked.values()))

    def test_two_outcomes_reaching_the_same_place_are_told_apart(self):
        """DEC-01 resolves two ways and both continue to DEC-02. Identified by the pair of boxes
        alone, one route would light the other's arrow -- saying the scenario took a branch it did
        not take, which is the kind of quiet wrongness a picture is trusted not to have."""
        intake = _intake([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Verified", ["DEC-02"], False),
            State("S-02", "DEC-01=Fail", "Unverified but continuing", ["DEC-02"], False),
            State("S-03", "DEC-02=Dispute", "Dispute filed", [], True, "Happy path"),
            State("S-04", "DEC-02=Unclear", "Handed over", [], True, "Escalation"),
        ])
        _, scenarios = self._scenarios(intake)
        walked = routes(intake, scenarios)

        crossings = {tuple(edge) for route in walked.values() for edge in route["edges"]
                     if edge[0] == "DEC-01" and edge[1] == "DEC-02"}
        self.assertEqual(crossings, {("DEC-01", "DEC-02", "Pass"), ("DEC-01", "DEC-02", "Fail")})
        for route in walked.values():
            taken = [edge[2] for edge in route["edges"] if edge[0] == "DEC-01"]
            self.assertEqual(len(taken), 1, "one route cannot take both outcomes of a decision")

    def test_the_drawing_says_which_outcome_each_arrow_is(self):
        """The route names an arrow by three things, so the SVG has to carry all three."""
        svg = render_svg(_SPLIT_ENDINGS)
        arrows = re.findall(r'<g class="graph__edge[^"]*" data-source="[^"]+" '
                            r'data-target="[^"]+" data-outcome="([^"]*)"', svg)
        self.assertEqual(len(arrows), len(build_layout(_SPLIT_ENDINGS).edges))
        self.assertIn("Pass", arrows)

    def test_a_scenario_with_no_path_is_left_out_rather_than_returned_empty(self):
        """A proposal the review added has no walk behind it yet. Left out, the page can tell
        "no route" from "a route that lights nothing"."""
        _, scenarios = self._scenarios(_SPLIT_ENDINGS)
        scenarios[0].path = []
        self.assertNotIn(scenarios[0].id, routes(_SPLIT_ENDINGS, scenarios))


class TestTheCardCarriesItsRoute(unittest.TestCase):
    """The list is the control and the graph is the readout, so the wiring between them is a
    stated attribute on the card rather than a request per click.

    Also pinned: the graph is *on* the stages that list scenarios. It used to be a thumbnail in
    the side panel there, which is not something a route can be read in — and a highlight nobody
    can see is the same as no highlight.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Routes"})

        scratch = Path(tempfile.mkdtemp())
        book = scratch / "intake.xlsx"
        write_template(str(book))
        sheet = load_workbook(book)
        sheet["L1 Use Case"]["B2"] = "Card servicing"
        sheet["Personas"].append(["P1", "Cardmember", "Happy path", "Yes"])
        sheet["L2 Capabilities"].append(["CAP-01", "Identity", "Gating"])
        sheet["L3 Decisions"].append(
            ["DEC-01", "Identity check", "CAP-01", "", "Pass / Fail", "User", 1, "", "No"])
        sheet["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
        sheet["L4 States"].append(["S-01", "DEC-01=Pass", "Verified", "", "Yes", "Happy path"])
        sheet["L4 States"].append(["S-02", "DEC-01=Fail", "Locked out", "", "Yes", "Termination"])
        sheet.save(book)
        with open(book, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, book.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")

        intake = read_intake(str(book))
        graph = DecisionGraph(intake.decisions, intake.states)
        walked, augmented = enumerate_paths(graph)
        scenarios = instantiate_all(walked, augmented, graph, intake.personas, intake.tools)
        workspace = next(self.root.iterdir())
        write_space_metadata(str(workspace / "scenario_space_metadata.xlsx"), intake, scenarios)
        state = json.loads((workspace / "workspace.json").read_text())
        state["stages"]["workflow"] = {"status": "complete",
                                       "artifacts": {"metadata": "scenario_space_metadata.xlsx"}}
        state["stages"]["scenarios"] = {"status": "complete",
                                        "artifacts": {"metadata": "scenario_space_metadata.xlsx"}}
        (workspace / "workspace.json").write_text(json.dumps(state))
        self.scenarios = scenarios

    def test_the_scenarios_come_from_their_own_workbook_and_carry_their_routes(self):
        """The second half of modular: point it at a scenario space as well and the page gains the
        list, where opening one lights the route it walks -- the same as the interface."""
        from scenario_generator.webapp.graphpage import write_graph_page

        workspace = next(self.root.iterdir())
        page = Path(tempfile.mkdtemp()) / "graph.html"
        write_graph_page(str(workspace / "intake.xlsx"), str(page),
                         str(workspace / "scenario_space_metadata.xlsx"))
        body = page.read_text()
        self.assertEqual(body.count("data-route='"), len(self.scenarios))
        self.assertIn("The scenarios that walk it", body)

    def test_a_scenario_the_drawing_cannot_place_is_left_out_of_the_list(self):
        """A proposal the review added has no walk behind it yet. Listed, it would look selectable
        and light nothing when opened, which reads as the page being broken rather than as the
        scenario having no route -- and the card would carry no route for the script to read."""
        from scenario_generator.core.models import ORIGIN_PROPOSED
        from scenario_generator.webapp.graphpage import render_graph_page
        from scenario_generator.core.intake import read_intake as _read

        workspace = next(self.root.iterdir())
        intake = _read(str(workspace / "intake.xlsx"))
        proposal = copy.deepcopy(self.scenarios[0])
        proposal.id, proposal.path, proposal.origin = "SC-999", [], ORIGIN_PROPOSED

        body = render_graph_page(intake, list(self.scenarios) + [proposal])
        self.assertEqual(body.count("data-route='"), len(self.scenarios))
        self.assertNotIn("SC-999", body)

    def test_without_a_scenario_workbook_it_is_the_graph_alone(self):
        """Not a broken list: the drawing on its own is a complete thing, and an empty section
        headed "the scenarios that walk it" reads as a page that failed to load them."""
        from scenario_generator.webapp.graphpage import write_graph_page

        workspace = next(self.root.iterdir())
        page = Path(tempfile.mkdtemp()) / "graph.html"
        write_graph_page(str(workspace / "intake.xlsx"), str(page))
        body = page.read_text()
        self.assertNotIn("data-route='", body)
        self.assertNotIn("The scenarios that walk it", body)
        self.assertIn("data-graph-canvas", body)

    def test_the_graph_is_drawn_on_a_stage_that_lists_scenarios(self):
        page = self.client.get("/stage/scenarios").data.decode()
        self.assertIn("data-graph-canvas", page)

    def test_every_card_states_the_route_it_walks(self):
        page = self.client.get("/stage/scenarios").data.decode()
        carried = re.findall(r"data-route='([^']+)'", page)
        self.assertEqual(len(carried), len(self.scenarios))
        route = json.loads(carried[0].replace("\\u0027", "'").replace("&#34;", '"'))
        self.assertEqual(route["edges"][0][0], "__start__")
        self.assertTrue(route["nodes"])

    def test_the_card_is_labelled_so_the_readout_can_name_what_is_lit(self):
        page = self.client.get("/stage/scenarios").data.decode()
        self.assertIn('data-route-label="%s"' % self.scenarios[0].id, page)


class TestTheGraphCanLeaveTheTool(unittest.TestCase):
    """The drawing on its own, as one file that still reads once it is out of the browser.

    A graph gets quoted in a validation report and mailed to a model owner, and both of those are
    done today with a screenshot -- which loses the zoom at exactly the size that needs one. What
    is pinned is that the served page carries everything it needs and asks for nothing.
    """

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.client = create_app(root).test_client()
        self.client.post("/workspaces", data={"name": "Portable graph"})

        book = Path(tempfile.mkdtemp()) / "intake.xlsx"
        write_template(str(book))
        sheet = load_workbook(book)
        sheet["L1 Use Case"]["B2"] = "Card servicing"
        sheet["Personas"].append(["P1", "Cardmember", "Happy path", "Yes"])
        sheet["L2 Capabilities"].append(["CAP-01", "Identity", "Gating"])
        sheet["L3 Decisions"].append(
            ["DEC-01", "Identity check", "CAP-01", "", "Pass / Fail", "User", 1, "", "No"])
        sheet["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
        sheet["L4 States"].append(["S-01", "DEC-01=Pass", "Verified", "", "Yes", "Happy path"])
        sheet["L4 States"].append(["S-02", "DEC-01=Fail", "Locked out", "", "Yes", "Termination"])
        sheet.save(book)
        with open(book, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, book.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")

    def test_it_is_built_from_the_workbooks_so_correcting_one_corrects_the_page(self):
        """The point of it being modular. A picture that was true once is a screenshot with extra
        steps; a page you rebuild from the sheet you just corrected is a thing to keep."""
        from openpyxl import load_workbook as _open
        from scenario_generator.webapp.graphpage import write_graph_page

        book = Path(tempfile.mkdtemp()) / "intake.xlsx"
        write_template(str(book))
        sheet = _open(book)
        sheet["L1 Use Case"]["B2"] = "Card servicing"
        sheet["Personas"].append(["P1", "Cardmember", "Happy path", "Yes"])
        sheet["L2 Capabilities"].append(["CAP-01", "Identity", "Gating"])
        sheet["L3 Decisions"].append(
            ["DEC-01", "Identity check", "CAP-01", "", "Pass / Fail", "User", 1, "", "No"])
        sheet["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
        sheet["L4 States"].append(["S-01", "DEC-01=Pass", "Verified", "", "Yes", "Happy path"])
        sheet["L4 States"].append(["S-02", "DEC-01=Fail", "Locked out", "", "Yes", "Termination"])
        sheet.save(book)

        first = Path(tempfile.mkdtemp()) / "graph.html"
        write_graph_page(str(book), str(first))
        self.assertIn("Identity check", first.read_text())

        sheet = _open(book)
        for row in sheet["L3 Decisions"].iter_rows(min_row=2):
            if row[0].value == "DEC-01":
                row[1].value = "Confirm who is calling"
        sheet.save(book)

        again = Path(tempfile.mkdtemp()) / "graph.html"
        write_graph_page(str(book), str(again))
        self.assertIn("Confirm who is calling", again.read_text())
        self.assertNotIn("Identity check", again.read_text())

    def test_the_page_carries_its_own_styling_and_its_own_controls(self):
        page = self.client.get("/graph").data.decode()
        self.assertIn("Card servicing", page)
        self.assertIn("data-zoom", page)                  # the reading controls came with it
        self.assertIn("graph__frame", page)
        # Nothing fetched. One <link> or <script src> and the file renders as unstyled markup the
        # moment somebody opens it from a mail attachment rather than from the tool.
        self.assertNotIn("<link", page)
        self.assertNotIn("<script src", page)

    def test_asking_for_it_as_a_download_sends_a_file_rather_than_a_page(self):
        response = self.client.get("/graph?download=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))

    def test_without_a_declaration_there_is_nothing_to_draw(self):
        client = create_app(Path(tempfile.mkdtemp())).test_client()
        client.post("/workspaces", data={"name": "Empty"})
        self.assertEqual(client.get("/graph").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class TestNothingIsDrawnOverAnythingElse(unittest.TestCase):
    """Swept over every example, in both drawings.

    Three defects, and each of them makes a picture that looks finished and is not readable: two
    boxes in the same place, an arrow running sideways between boxes on one row, and two labels
    stacked on top of each other so the wider reads as nonsense running through the other. None
    of them fails anything; they are only visible by looking, which is why they are measured here.
    """

    EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "intakes"

    def _drawings(self):
        from scenario_generator.core.intake import read_intake
        from scenario_generator.webapp.graphview import build_block_layout, build_layout

        for path in sorted(self.EXAMPLES.glob("*.xlsx")):
            intake = read_intake(str(path))
            yield f"{path.name} (every decision)", build_layout(intake)
            blocks = build_block_layout(intake)
            if blocks.nodes:
                yield f"{path.name} (capabilities)", blocks

    def test_no_two_boxes_are_in_the_same_place(self):
        from scenario_generator.webapp.graphview import BOX_HEIGHT, BOX_WIDTH

        for name, layout in self._drawings():
            boxes = list(layout.nodes.values())
            for index, one in enumerate(boxes):
                for other in boxes[index + 1:]:
                    apart = (abs(one.x - other.x) >= BOX_WIDTH - 1
                             or abs(one.y - other.y) >= BOX_HEIGHT - 1)
                    self.assertTrue(apart, f"{name}: {one.id} and {other.id} overlap")

    def test_no_arrow_runs_sideways(self):
        """An arrow between two boxes on the same row reads as a connection between neighbours
        rather than as a step forward, and its label has nowhere to sit. It means the depths were
        assigned wrongly -- which used to happen to every node the walk could not reach, because
        they were all parked on one line together."""
        for name, layout in self._drawings():
            for edge in layout.edges:
                source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
                if not source or not target or edge.is_back:
                    continue
                self.assertNotEqual(source.y, target.y,
                                    f"{name}: {edge.source} -> {edge.target} runs sideways")

    def test_no_two_labels_are_drawn_over_each_other(self):
        from scenario_generator.webapp.graphview import (_label_position, _label_width, _stagger)

        for name, layout in self._drawings():
            rank_of = _stagger(layout)
            placed = []
            for edge in layout.edges:
                source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
                if not source or not target:
                    continue
                x, y = _label_position(edge, source, target, rank_of.get(id(edge), 0))
                width = _label_width(edge)
                for other_x, other_y, other_width, other in placed:
                    clear = (abs(y - other_y) >= 16
                             or abs(x - other_x) >= (width + other_width) / 2 - 2)
                    self.assertTrue(clear, f"{name}: the labels on {other} and "
                                           f"{edge.source} -> {edge.target} are drawn over "
                                           f"each other")
                placed.append((x, y, width, f"{edge.source} -> {edge.target}"))

    def test_a_fan_whose_labels_do_not_touch_stays_on_one_line(self):
        """Every extra row puts a label further from the arrow it belongs to, so the stagger is
        packed rather than assigned by position."""
        from scenario_generator.core.models import Decision, IntakeData, State
        from scenario_generator.webapp.graphview import build_layout, _stagger

        intake = IntakeData(
            use_case={"Use case name": "Wide"}, personas=[], capabilities=[],
            decisions=[Decision(id="DEC-01", name="Pick", trigger_capability="", inputs="",
                                variants=["A", "B", "C"])],
            states=[State(id="S-00", reached_via="Start", description="Opens",
                          next_decisions=["DEC-01"], is_terminal=False),
                    State(id="S-01", reached_via="DEC-01=A", description="A",
                          next_decisions=[], is_terminal=True, outcome_type="Happy path"),
                    State(id="S-02", reached_via="DEC-01=B", description="B",
                          next_decisions=[], is_terminal=True, outcome_type="Termination"),
                    State(id="S-03", reached_via="DEC-01=C", description="C",
                          next_decisions=[], is_terminal=True, outcome_type="Escalation")],
            tools=[])
        layout = build_layout(intake)
        branches = [e for e in layout.edges if e.source == "DEC-01"]
        self.assertEqual(len(branches), 3)
        self.assertEqual({_stagger(layout).get(id(e), 0) for e in branches}, {0},
                         "three short labels that do not touch were staggered anyway")
