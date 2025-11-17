"""
Conversation Memory Manager for AWS TagSense.

This module manages conversation history, allowing the AI assistant to maintain
context across multiple turns in a chat session. This enables more natural,
context-aware conversations.
"""

from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json


@dataclass
class ConversationTurn:
    """Represents a single turn in a conversation.

    Attributes:
        role: The role (user or assistant)
        content: The message content
        timestamp: When this turn occurred
        metadata: Optional metadata (e.g., model used, tokens, etc.)
    """
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ConversationTurn':
        """Create from dictionary."""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {})
        )


class ConversationManager:
    """Manages conversation history for chat sessions.

    This class maintains a sliding window of recent conversation turns,
    enabling the AI to reference previous questions and answers for
    better context awareness.

    Example:
        ```python
        manager = ConversationManager(max_history=10)

        # Add user message
        manager.add_turn("user", "What are the risks of untagged EC2 instances?")

        # Add assistant response
        manager.add_turn("assistant", "Untagged EC2 instances pose several risks...")

        # Get conversation history
        history = manager.get_history()

        # Clear history
        manager.clear()
        ```
    """

    def __init__(self, max_history: int = 10):
        """Initialize conversation manager.

        Args:
            max_history: Maximum number of conversation turns to keep
        """
        self.max_history = max_history
        self.turns: List[ConversationTurn] = []
        self.session_start = datetime.now()

    def add_turn(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ) -> None:
        """Add a conversation turn to history.

        Args:
            role: Role of the speaker ("user" or "assistant")
            content: The message content
            metadata: Optional metadata about this turn
        """
        if role not in ["user", "assistant", "system"]:
            raise ValueError(f"Invalid role: {role}. Must be 'user', 'assistant', or 'system'")

        turn = ConversationTurn(
            role=role,
            content=content,
            metadata=metadata or {}
        )

        self.turns.append(turn)

        # Trim history if it exceeds max_history
        if len(self.turns) > self.max_history:
            # Keep system messages and trim from the oldest user/assistant turns
            system_turns = [t for t in self.turns if t.role == "system"]
            other_turns = [t for t in self.turns if t.role != "system"]

            # Keep most recent turns
            trimmed_others = other_turns[-(self.max_history - len(system_turns)):]
            self.turns = system_turns + trimmed_others

    def get_history(self, include_system: bool = False) -> List[ConversationTurn]:
        """Get conversation history.

        Args:
            include_system: Whether to include system messages in history

        Returns:
            List of conversation turns
        """
        if include_system:
            return self.turns.copy()
        else:
            return [t for t in self.turns if t.role != "system"]

    def get_recent(self, n: int = 3) -> List[ConversationTurn]:
        """Get the N most recent conversation turns.

        Args:
            n: Number of recent turns to retrieve

        Returns:
            List of recent conversation turns
        """
        return self.turns[-n:] if self.turns else []

    def get_summary(self) -> str:
        """Get a summary of the conversation.

        Returns:
            A text summary of the conversation
        """
        if not self.turns:
            return "No conversation history"

        user_turns = len([t for t in self.turns if t.role == "user"])
        assistant_turns = len([t for t in self.turns if t.role == "assistant"])
        duration = (datetime.now() - self.session_start).seconds

        return (
            f"Conversation: {user_turns} user messages, {assistant_turns} assistant responses. "
            f"Duration: {duration // 60}m {duration % 60}s"
        )

    def format_for_llm(self) -> List[Dict[str, str]]:
        """Format conversation history for LLM input.

        Returns:
            List of message dictionaries in LLM format
        """
        return [
            {"role": turn.role, "content": turn.content}
            for turn in self.turns
        ]

    def clear(self) -> None:
        """Clear all conversation history."""
        self.turns = []
        self.session_start = datetime.now()

    def export_json(self) -> str:
        """Export conversation history as JSON.

        Returns:
            JSON string representation of the conversation
        """
        return json.dumps({
            "session_start": self.session_start.isoformat(),
            "turns": [turn.to_dict() for turn in self.turns],
            "summary": self.get_summary()
        }, indent=2)

    def import_json(self, json_str: str) -> None:
        """Import conversation history from JSON.

        Args:
            json_str: JSON string to import
        """
        data = json.loads(json_str)
        self.session_start = datetime.fromisoformat(data["session_start"])
        self.turns = [ConversationTurn.from_dict(t) for t in data["turns"]]

    def __len__(self) -> int:
        """Get number of turns in conversation."""
        return len(self.turns)

    def __repr__(self) -> str:
        """Developer-friendly representation."""
        return f"ConversationManager(turns={len(self.turns)}, max_history={self.max_history})"
