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

F1 Location Mapping (users may refer to a race by ANY of these names):
  "Abu Dhabi" / "Yas Marina"                  → grand_prix = "Abu Dhabi Grand Prix", circuit = "Yas Marina Circuit"
  "Australia" / "Melbourne" / "Albert Park"    → grand_prix = "Australian Grand Prix", circuit = "Albert Park Grand Prix Circuit"
  "Austria" / "Spielberg" / "Red Bull Ring"    → grand_prix = "Austrian Grand Prix", circuit = "Red Bull Ring"
  "Azerbaijan" / "Baku"                        → grand_prix = "Azerbaijan Grand Prix", circuit = "Baku City Circuit"
  "Bahrain" / "Sakhir"                         → grand_prix = "Bahrain Grand Prix", circuit = "Bahrain International Circuit"
  "Belgium" / "Spa" / "Spa-Francorchamps"      → grand_prix = "Belgian Grand Prix", circuit = "Circuit de Spa-Francorchamps"
  "Britain" / "UK" / "Silverstone"             → grand_prix = "British Grand Prix", circuit = "Silverstone Circuit"
  "Canada" / "Montreal"                        → grand_prix = "Canadian Grand Prix", circuit = "Circuit Gilles Villeneuve"
  "China" / "Shanghai"                         → grand_prix = "Chinese Grand Prix", circuit = "Shanghai International Circuit"
  "Netherlands" / "Dutch" / "Zandvoort"        → grand_prix = "Dutch Grand Prix", circuit = "Circuit Park Zandvoort"
  "Emilia Romagna" / "Imola"                   → grand_prix = "Emilia Romagna Grand Prix", circuit = "Autodromo Enzo e Dino Ferrari"
  "Hungary" / "Budapest" / "Hungaroring"       → grand_prix = "Hungarian Grand Prix", circuit = "Hungaroring"
  "Italy" / "Monza"                            → grand_prix = "Italian Grand Prix", circuit = "Autodromo Nazionale di Monza"
  "Japan" / "Suzuka"                           → grand_prix = "Japanese Grand Prix", circuit = "Suzuka Circuit"
  "Las Vegas" / "Vegas"                        → grand_prix = "Las Vegas Grand Prix", circuit = "Las Vegas Strip Street Circuit"
  "Mexico" / "Mexico City"                     → grand_prix = "Mexico City Grand Prix", circuit = "Autódromo Hermanos Rodríguez"
  "Miami"                                      → grand_prix = "Miami Grand Prix", circuit = "Miami International Autodrome"
  "Monaco" / "Monte Carlo"                     → grand_prix = "Monaco Grand Prix", circuit = "Circuit de Monaco"
  "Qatar" / "Lusail" / "Losail"                → grand_prix = "Qatar Grand Prix", circuit = "Losail International Circuit"
  "Saudi Arabia" / "Jeddah"                    → grand_prix = "Saudi Arabian Grand Prix", circuit = "Jeddah Corniche Circuit"
  "Singapore" / "Marina Bay"                   → grand_prix = "Singapore Grand Prix", circuit = "Marina Bay Street Circuit"
  "Spain" / "Barcelona" / "Catalunya"           → grand_prix = "Spanish Grand Prix", circuit = "Circuit de Barcelona-Catalunya"
  "Brazil" / "São Paulo" / "Sao Paulo" / "Interlagos" → grand_prix = "São Paulo Grand Prix", circuit = "Autódromo José Carlos Pace"
  "USA" / "Austin" / "COTA"                    → grand_prix = "United States Grand Prix", circuit = "Circuit of the Americas"

Rules:
- Return ONLY the raw SQL query, no markdown, no explanation.
- Use LIKE for partial text matching (e.g., driver LIKE '%Verstappen%').
- When a user mentions a location, track, city, or country, use the mapping above to match against the EXACT grand_prix or circuit value. Always check BOTH columns using OR.
- For "wins", use finish_position = 1.
- Always include relevant columns in SELECT for context.
- Note: "Monza" refers to the Italian GP, NOT Imola. "Imola" is the Emilia Romagna GP. These are two different races in Italy.
"""


class QueryDataTool(BaseTool):
    """Queries the local F1 SQLite database using LLM-generated SQL."""

    @property
    def name(self) -> str:
        return "query_data"

    @property
    def description(self) -> str:
        return (
            "Query structured F1 race results (2023-2024). "
            "USE THIS FOR: Hard numerical statistics, standings, driver points, race counts, grid/finish positions, and constructor performance. "
            "DO NOT USE THIS FOR: Explanations of 'why' a driver won/lost, subjective opinions, team strategies, track conditions, or events occurring outside the 2023-2024 seasons."
        )

    def run(self, query: str) -> str:
        if not os.path.exists(DB_PATH):
            return "ERROR: Database not found. Run 'python -m indexing.load_data' first."

        try:
            # Step 1: LLM generates SQL
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(
                f"{SCHEMA_PROMPT}\n\nUser question: {query}"
            )
            sql = response.text.strip()
            if sql.startswith("```"):
                sql = "\n".join(sql.split("\n")[1:-1])
            sql = sql.strip()
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
