"""Document ingestion: submitted files in, verified evidence out.

The stage that runs before everything else. It reads the pack a model owner submits, extracts
what the scenario space needs, checks every claim against the document it cites, and produces three
things: the evidence record, a cited context document for the later stages, and the questions
nobody's documents answered.

Nothing here trusts a model's word about what a document says. Extraction proposes; the
deterministic check in :mod:`~scenario_generator.core.grounding` disposes.
"""
from .context_document import (build_context_document, build_model_context,
                               open_questions, rejection_summary)
from .conversations import (Conversation, Turn, UnreadableConversations,
                            read_conversations, redact_conversations)
from .drafting import (DraftedIntake, carry_forward, draft_intake, repair_intake,
                       revise_intake,
                       write_drafted_intake)
from .extraction import (DocumentExtractor, IngestionFailed, build_corpus,
                         extract_documents, record_from_json, record_to_json)
from .readers import SUPPORTED_EXTENSIONS, UnreadableDocument, read_document

__all__ = ["DocumentExtractor", "extract_documents", "record_to_json", "record_from_json",
           "build_context_document", "build_model_context", "open_questions",
           "rejection_summary", "IngestionFailed",
           "read_document", "UnreadableDocument", "SUPPORTED_EXTENSIONS",
           "build_corpus",
           "draft_intake", "repair_intake", "revise_intake", "write_drafted_intake",
           "DraftedIntake", "carry_forward", "redact_conversations",
           "read_conversations", "UnreadableConversations", "Conversation", "Turn"]
