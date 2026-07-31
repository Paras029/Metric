"""Command-line entry point: `python -m scenario_generator <command> ...`. Argument parsing only —
all real logic lives in pipeline.py.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import List, Optional

from .core import write_template
from .ingest import SUPPORTED_EXTENSIONS
from .llm import NullMaterialityAssessor, NullReviewer, NullWriter, metering
from .pipeline import (assess_materiality, build_graph, build_pack, build_probes_stage,
                       draft_intake_workbook, generate, ingest_documents, map_coverage, refine,
                       review, revise_intake_workbook)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="scenario_generator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_template = sub.add_parser("init-template", help="write a blank intake workbook")
    p_template.add_argument("output")

    p_graph = sub.add_parser("build-graph",
                             help="stage 1: deterministic metadata + graph traversal (no LLM)")
    p_graph.add_argument("intake")
    p_graph.add_argument("graph_output")
    p_graph.add_argument("--with-probes", action="store_true",
                         help="also generate applicable adversarial probes")

    p_probes = sub.add_parser("build-probes",
                              help="append applicable adversarial probes to a graph file (no LLM)")
    p_probes.add_argument("intake")
    p_probes.add_argument("graph_input")
    p_probes.add_argument("graph_output", help="pass the same path as graph_input to update in place")

    p_refine = sub.add_parser("refine",
                              help="stage 2: LLM description and turn plan on a graph file")
    p_refine.add_argument("intake")
    p_refine.add_argument("graph_input")
    p_refine.add_argument("output_prefix")
    p_refine.add_argument("--no-llm", action="store_true",
                          help="skip the LLM writer (deterministic text)")
    p_refine.add_argument("--context", help="optional business context file (txt or md)")
    p_refine.add_argument("--note", action="append", metavar="TEXT",
                        help="anything the documents do not say that this pass should know; repeatable")

    p_mat = sub.add_parser("assess-materiality",
                           help="stage: a separate LLM sweep assigning materiality on a registry")
    p_mat.add_argument("intake")
    p_mat.add_argument("registry_input")
    p_mat.add_argument("registry_output", help="pass the same path as registry_input to update in place")
    p_mat.add_argument("--no-llm", action="store_true",
                       help="skip the materiality sweep (leave existing values)")
    p_mat.add_argument("--context", help="optional business context file (txt or md)")
    p_mat.add_argument("--note", action="append", metavar="TEXT",
                        help="anything the documents do not say that this pass should know; repeatable")

    p_gen = sub.add_parser("generate",
                           help="one-shot: build-graph + refine + assess-materiality in memory")
    p_gen.add_argument("intake")
    p_gen.add_argument("output_prefix")
    p_gen.add_argument("--no-llm", action="store_true",
                       help="skip all LLM calls (deterministic text, default materiality)")
    p_gen.add_argument("--with-probes", action="store_true",
                       help="also generate applicable adversarial probes")
    p_gen.add_argument("--context", help="optional business context file (txt or md)")
    p_gen.add_argument("--note", action="append", metavar="TEXT",
                        help="anything the documents do not say that this pass should know; repeatable")

    p_review = sub.add_parser("review",
                              help="final stage: whole-registry LLM sweep — revises materiality "
                                   "and proposes additions")
    p_review.add_argument("intake")
    p_review.add_argument("registry_input")
    p_review.add_argument("registry_output",
                          help="pass the same path as registry_input to update in place")
    p_review.add_argument("--no-llm", action="store_true", help="skip the review sweep")
    p_review.add_argument("--context", help="optional business context file (txt or md)")
    p_review.add_argument("--note", action="append", metavar="TEXT",
                        help="anything the documents do not say that this pass should know; repeatable")
    p_review.add_argument("--max-proposals", type=int, default=None,
                          help="cap on scenarios the review may add (default 15)")
    p_review.add_argument("--pack", help="also rebuild the challenge pack at this path")
    p_review.add_argument("--owner-scenarios",
                          help="optional: the owner's own scenario library, shown as context")

    p_pack = sub.add_parser("build-pack",
                            help="write the challenge pack from a registry (no LLM)")
    p_pack.add_argument("intake")
    p_pack.add_argument("registry")
    p_pack.add_argument("pack_output")

    p_map = sub.add_parser("map-coverage",
                           help="stage: map an owner scenario library onto a generated registry")
    p_map.add_argument("intake")
    p_map.add_argument("registry")
    p_map.add_argument("owner")
    p_map.add_argument("report")

    p_ingest = sub.add_parser(
        "ingest",
        help="stage 0: read submitted documents into evidence, context and open questions")
    p_ingest.add_argument("sources", nargs="+",
                          help="document files, or directories to read them from "
                               f"({', '.join(SUPPORTED_EXTENSIONS)})")
    p_ingest.add_argument("output_prefix",
                          help="written as PREFIX_evidence.json, PREFIX_context.md and "
                               "PREFIX_questions.md")
    p_ingest.add_argument("--resolve-passes", type=int, default=None, metavar="N",
                          help="how many times a question the first reading left open is put "
                               "back to the documents before it is put to the model owner "
                               "(default from LLM_INGEST_RESOLVE_PASSES). 0 skips the sweep and "
                               "asks everything.")

    p_draft = sub.add_parser(
        "draft-intake",
        help="draft an intake workbook from an ingested context document")
    p_draft.add_argument("context", help="the PREFIX_context.md written by ingest")
    p_draft.add_argument("output", help="where to write the drafted intake workbook")
    p_draft.add_argument("--evidence", default=None,
                         help="the PREFIX_evidence.json written by ingest, for the decision graph "
                              "read out of any submitted diagrams (default: found beside context)")
    p_draft.add_argument("--note", action="append", metavar="TEXT",
                         help="something you know that the documents do not say, or an answer to "
                              "one of the open questions -- may be given more than once")

    p_revise = sub.add_parser(
        "revise-intake",
        help="revise an existing intake workbook with new context, keeping what is unaffected")
    p_revise.add_argument("current", help="the intake workbook to revise")
    p_revise.add_argument("output", help="where to write the revised workbook -- "
                                         "may be the same path, to revise it in place")
    p_revise.add_argument("--context", default=None,
                          help="the PREFIX_context.md written by ingest, if anything has changed")
    p_revise.add_argument("--evidence", default=None,
                          help="the PREFIX_evidence.json written by ingest, for the decision graph "
                               "read out of any submitted diagrams (default: found beside context)")
    p_revise.add_argument("--note", action="append", metavar="TEXT",
                          help="an answer to a gap the current declaration left, or anything else "
                               "learned since it was last written -- may be given more than once")

    p_serve = sub.add_parser("serve", help="run the local web interface")
    p_serve.add_argument("--port", type=int, default=5000)
    p_serve.add_argument("--workspaces", default="workspaces",
                         help="directory holding one folder per use case (default: workspaces)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "serve":
        from .webapp.app import create_app          # imported here so the CLI works without Flask
        app = create_app(Path(args.workspaces))
        print(f"\n  Scenario generator — http://127.0.0.1:{args.port}\n")
        app.run(host="127.0.0.1", port=args.port)
        return 0

    # Every other command finishes, so it is worth saying what it spent. A count of zero means the
    # command was deterministic (or was given --no-llm), which is not worth a line of its own.
    with metering.counted() as calls:
        code = _run_command(args)
    if calls():
        print(f"\n{calls()} model call(s).")
    return code


def _run_command(args) -> int:
    if args.command == "init-template":
        write_template(args.output)
        print(f"Wrote blank intake template to {args.output}")
        return 0

    if args.command == "build-graph":
        build_graph(args.intake, args.graph_output, with_probes=args.with_probes)
        return 0

    if args.command == "build-probes":
        build_probes_stage(args.intake, args.graph_input, args.graph_output)
        return 0

    if args.command == "refine":
        refine(args.intake, args.graph_input, args.output_prefix,
              writer=NullWriter() if args.no_llm else None, context_path=args.context, notes=args.note)
        return 0

    if args.command == "assess-materiality":
        assess_materiality(args.intake, args.registry_input, args.registry_output,
                          assessor=NullMaterialityAssessor() if args.no_llm else None,
                          context_path=args.context, notes=args.note)
        return 0

    if args.command == "review":
        review(args.intake, args.registry_input, args.registry_output,
              reviewer=NullReviewer() if args.no_llm else None,
              context_path=args.context, notes=args.note, proposal_limit=args.max_proposals,
              owner_scenarios_path=args.owner_scenarios, pack_path=args.pack)
        return 0

    if args.command == "build-pack":
        build_pack(args.intake, args.registry, args.pack_output)
        return 0

    if args.command == "map-coverage":
        map_coverage(args.intake, args.registry, args.owner, args.report)
        return 0

    if args.command == "ingest":
        paths = _collect_documents(args.sources)
        if not paths:
            print("No readable documents found in what you gave me.")
            return 1
        ingest_documents(paths, args.output_prefix, progress=_print_progress,
                         resolve_passes=args.resolve_passes)
        return 0

    if args.command == "draft-intake":
        draft_intake_workbook(args.context, args.output, evidence_path=args.evidence,
                              notes=args.note)
        print(f"Drafted {args.output}. Read the 'Review This' sheet before relying on it.")
        return 0

    if args.command == "revise-intake":
        revise_intake_workbook(args.current, args.output, context_path=args.context,
                               evidence_path=args.evidence, notes=args.note)
        print(f"Revised {args.output}. Read the 'Review This' sheet before relying on it.")
        return 0

    generate(args.intake, args.output_prefix,
            writer=NullWriter() if args.no_llm else None,
            assessor=NullMaterialityAssessor() if args.no_llm else None,
            with_probes=args.with_probes, context_path=args.context, notes=args.note)
    return 0


def _collect_documents(sources: List[str]) -> List[str]:
    """Expand whatever was named on the command line into a list of readable files.

    A directory is read one level deep rather than recursively: a submitted pack is a folder of
    documents, and walking into subdirectories tends to pick up archives and working copies the
    sender did not mean to include.
    """
    found: List[str] = []
    for source in sources:
        path = Path(source)
        if path.is_dir():
            found.extend(str(child) for child in sorted(path.iterdir())
                         if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS)
        elif path.is_file():
            found.append(str(path))
        else:
            logging.getLogger("scenario_generator").warning("Skipping %s: not found.", source)
    return found


def _print_progress(message: str) -> None:
    """Ingestion is slow enough that silence reads as a hang."""
    print(f"  {message}")
