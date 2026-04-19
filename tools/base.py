"""Base tool interface — every tool implements this contract."""
from abc import ABC, abstractmethod


class BaseTool(ABC):
    """Abstract base class for all agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier (e.g., 'query_data')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description the LLM reads to decide when to use this tool."""
        ...

    @abstractmethod
    def run(self, query: str) -> str:
        """Execute the tool with a natural language query. Returns a string result."""
        ...

    def __repr__(self) -> str:
        return f"<Tool: {self.name}>"
