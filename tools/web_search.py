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
            "Search the live web for current F1 information via Tavily. "
            "USE THIS FOR: Breaking news, real-time standings, recent events outside 2024-2025, or fallback when local data/documents fail. "
            "DO NOT USE THIS FOR: First-pass data retrieval for 2024-2025. ALWAYS try `query_data` and `search_docs` first for those seasons!"
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
                if len(content) > 500:
                    content = content[:500] + "..."
                url = r.get("url", "")
                pub_date = r.get("published_date", "Date unknown")
                output_parts.append(f"**{title}**\n{content}\nURL: {url}\nPublished: {pub_date}")

            return "\n\n---\n\n".join(output_parts)

        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
