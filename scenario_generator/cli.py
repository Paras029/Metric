"""Command-line entry point: `python -m scenario_generator <command> ...`. Argument parsing only —
all real logic lives in pipeline.py.
"""
from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from .core import write_template
from .llm import NullMaterialityAssessor, NullReviewer, NullWriter
from .pipeline import (assess_materiality, build_graph, build_pack, build_probes_stage,
                       generate, map_coverage, refine, review)


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

    p_mat = sub.add_parser("assess-materiality",
                           help="stage: a separate LLM sweep assigning materiality on a registry")
    p_mat.add_argument("intake")
    p_mat.add_argument("registry_input")
    p_mat.add_argument("registry_output", help="pass the same path as registry_input to update in place")
    p_mat.add_argument("--no-llm", action="store_true",
                       help="skip the materiality sweep (leave existing values)")
    p_mat.add_argument("--context", help="optional business context file (txt or md)")

    p_gen = sub.add_parser("generate",
                           help="one-shot: build-graph + refine + assess-materiality in memory")
    p_gen.add_argument("intake")
    p_gen.add_argument("output_prefix")
    p_gen.add_argument("--no-llm", action="store_true",
                       help="skip all LLM calls (deterministic text, default materiality)")
    p_gen.add_argument("--with-probes", action="store_true",
                       help="also generate applicable adversarial probes")
    p_gen.add_argument("--context", help="optional business context file (txt or md)")

    p_review = sub.add_parser("review",
                              help="final stage: whole-registry LLM sweep — revises materiality "
                                   "and proposes additions")
    p_review.add_argument("intake")
    p_review.add_argument("registry_input")
    p_review.add_argument("registry_output",
                          help="pass the same path as registry_input to update in place")
    p_review.add_argument("--no-llm", action="store_true", help="skip the review sweep")
    p_review.add_argument("--context", help="optional business context file (txt or md)")
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

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

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
              writer=NullWriter() if args.no_llm else None, context_path=args.context)
        return 0

    if args.command == "assess-materiality":
        assess_materiality(args.intake, args.registry_input, args.registry_output,
                          assessor=NullMaterialityAssessor() if args.no_llm else None,
                          context_path=args.context)
        return 0

    if args.command == "review":
        review(args.intake, args.registry_input, args.registry_output,
              reviewer=NullReviewer() if args.no_llm else None,
              context_path=args.context, proposal_limit=args.max_proposals,
              owner_scenarios_path=args.owner_scenarios, pack_path=args.pack)
        return 0

    if args.command == "build-pack":
        build_pack(args.intake, args.registry, args.pack_output)
        return 0

    if args.command == "map-coverage":
        map_coverage(args.intake, args.registry, args.owner, args.report)
        return 0

    generate(args.intake, args.output_prefix,
            writer=NullWriter() if args.no_llm else None,
            assessor=NullMaterialityAssessor() if args.no_llm else None,
            with_probes=args.with_probes, context_path=args.context)
    return 0
