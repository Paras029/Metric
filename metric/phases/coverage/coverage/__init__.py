"""Map the owner's own transcripts onto the scenario space."""
from metric.phases.coverage.coverage.conversations import (Conversation, Turn,
                                                           UnreadableConversations,
                                                           read_conversations,
                                                           redact_conversations)

__all__ = ["Conversation", "Turn", "UnreadableConversations", "read_conversations",
           "redact_conversations"]
