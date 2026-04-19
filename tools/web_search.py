"""Tool 3: Live web search using Tavily API (designed for LLM agents)."""
import os

from dotenv import load_dotenv
from tools.base import BaseTool

load_dotenv()


class WebSearchTool(BaseTool):
    """Searches the live web for current F1 information via Tavily."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the live web for current F1 information. Use for: "
            "current standings, live results, breaking news, recent events, "
            "anything not in the local database or documents."
        )

    def run(self, query: str, max_results: int = 3) -> str:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return "ERROR: TAVILY_API_KEY not set in .env"

        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=api_key)
            response = client.search(
                query=f"Formula 1 {query}",
                max_results=max_results,
                search_depth="basic",
            )

            results = response.get("results", [])
            if not results:
                return "No web results found."

            output_parts = []
            for r in results:
                title = r.get("title", "No title")
                content = r.get("content", "No content")
                url = r.get("url", "")
                output_parts.append(f"**{title}**\n{content}\nURL: {url}")

            return "\n\n---\n\n".join(output_parts)

        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
