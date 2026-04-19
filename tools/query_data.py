"""Tool 1: Query structured F1 race data via natural language to SQL."""
import os
import sqlite3

import google.generativeai as genai
from dotenv import load_dotenv
from tools.base import BaseTool

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

DB_PATH = "data/structured/f1_results.db"

SCHEMA_PROMPT = """You are a SQL expert. Convert the user's question into a SQLite query.

Table: race_results
Columns:
  season (INT)           -- 2023 or 2024
  round (INT)            -- round number in the season
  grand_prix (TEXT)      -- e.g. "Bahrain Grand Prix"
  circuit (TEXT)         -- e.g. "Bahrain International Circuit"
  date (TEXT)            -- race date, e.g. "2023-03-05"
  driver (TEXT)          -- full name, e.g. "Max Verstappen"
  driver_code (TEXT)     -- 3-letter code, e.g. "VER"
  driver_nationality (TEXT)
  constructor (TEXT)     -- team name, e.g. "Red Bull"
  grid_position (INT)   -- starting position
  finish_position (INT) -- finishing position (1 = winner)
  position_text (TEXT)   -- "1", "2", ... or "R" for retired
  points (REAL)          -- points scored
  laps_completed (INT)
  status (TEXT)          -- "Finished", "+1 Lap", "Collision", etc.
  fastest_lap_rank (TEXT)
  fastest_lap_time (TEXT)

Rules:
- Return ONLY the raw SQL query, no markdown, no explanation.
- Use LIKE for partial text matching (e.g., driver LIKE '%Verstappen%').
- For "wins", use finish_position = 1.
- Always include relevant columns in SELECT for context.
"""


class QueryDataTool(BaseTool):
    """Queries the local F1 SQLite database using LLM-generated SQL."""

    @property
    def name(self) -> str:
        return "query_data"

    @property
    def description(self) -> str:
        return (
            "Query structured F1 race results (2023-2024). Use for stats, "
            "standings, race counts, points, grid positions, constructors."
        )

    def run(self, query: str) -> str:
        if not os.path.exists(DB_PATH):
            return "ERROR: Database not found. Run 'python -m indexing.load_data' first."

        try:
            # Step 1: LLM generates SQL
            model = genai.GenerativeModel("gemini-2.0-flash-lite")
            response = model.generate_content(
                f"{SCHEMA_PROMPT}\n\nUser question: {query}"
            )
            sql = response.text.strip().strip("`").replace("sql\n", "").replace("sql", "", 1).strip()
            print(f"  [query_data] SQL: {sql}")

            # Step 2: Execute SQL
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return f"Query returned no results.\nSQL used: {sql}"

            # Step 3: Format results
            result_lines = [" | ".join(columns)]
            result_lines.append("-" * len(result_lines[0]))
            for row in rows[:25]:  # Cap at 25 rows
                result_lines.append(" | ".join(str(v) for v in row))

            return f"SQL: {sql}\nResults ({len(rows)} rows):\n" + "\n".join(result_lines)

        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
