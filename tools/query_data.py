"""Tool 1: Query structured F1 race data via natural language to SQL."""
import os
import re
import sqlite3
from utils.llm import generate_llm_response
from dotenv import load_dotenv
from tools.base import BaseTool

load_dotenv()

DB_PATH = "data/structured/f1_results.db"

SCHEMA_PROMPT = """You are a SQL expert. Convert the user's question into a SQLite query.

Table: race_results
Columns:
  season (INT)           -- 2024 or 2025
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
- For "podiums", use finish_position <= 3 (P1, P2, and P3 are ALL podiums — wins count as podiums).
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
            "Query structured F1 race results (2024-2025). "
            "USE THIS FOR: Hard numerical statistics, standings, driver points, race counts, grid/finish positions, and constructor performance. "
            "DO NOT USE THIS FOR: Explanations of 'why' a driver won/lost, subjective opinions, team strategies, track conditions, or events occurring outside the 2024-2025 seasons."
        )

    def run(self, query: str) -> str:
        if not os.path.exists(DB_PATH):
            # Auto-build the DB from the committed CSV on first run (fresh clone)
            csv_path = "data/structured/f1_results.csv"
            if not os.path.exists(csv_path):
                return "ERROR: Neither f1_results.db nor f1_results.csv found. Please run 'python -m indexing.fetch_f1_data' to fetch data first."
            try:
                from indexing.load_data import load_csv_to_sqlite
                print("  [query_data] DB not found — auto-building from CSV...")
                load_csv_to_sqlite(csv_path=csv_path, db_path=DB_PATH)
            except Exception as e:
                return f"ERROR: Failed to auto-build database: {e}"

        # Guard: block raw SQL injection in the user query itself
        query_upper = query.upper()
        RAW_FORBIDDEN = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE"]
        if any(kw in query_upper for kw in RAW_FORBIDDEN) and ";" in query:
            return "ERROR: Query looks like a SQL injection attempt. Ask in plain English."

        try:
            # Step 0: Resolve location aliases locally (avoid LLM dependency for simple mappings)
            ALIAS_MAP = {
                "spa": "Belgian Grand Prix at Circuit de Spa-Francorchamps",
                "silverstone": "British Grand Prix at Silverstone Circuit",
                "monza": "Italian Grand Prix at Autodromo Nazionale di Monza",
                "imola": "Emilia Romagna Grand Prix at Autodromo Enzo e Dino Ferrari",
                "baku": "Azerbaijan Grand Prix at Baku City Circuit",
                "jeddah": "Saudi Arabian Grand Prix at Jeddah Corniche Circuit",
                "monaco": "Monaco Grand Prix at Circuit de Monaco",
                "suzuka": "Japanese Grand Prix at Suzuka Circuit",
                "interlagos": "São Paulo Grand Prix at Autódromo José Carlos Pace",
                "zandvoort": "Dutch Grand Prix at Circuit Park Zandvoort",
                "marina bay": "Singapore Grand Prix at Marina Bay Street Circuit",
                "albert park": "Australian Grand Prix at Albert Park Grand Prix Circuit",
                "melbourne": "Australian Grand Prix at Albert Park Grand Prix Circuit",
                "vegas": "Las Vegas Grand Prix at Las Vegas Strip Street Circuit",
                "las vegas": "Las Vegas Grand Prix at Las Vegas Strip Street Circuit",
                "cota": "United States Grand Prix at Circuit of the Americas",
                "austin": "United States Grand Prix at Circuit of the Americas",
                "hungaroring": "Hungarian Grand Prix at Hungaroring",
                "budapest": "Hungarian Grand Prix at Hungaroring",
                "barcelona": "Spanish Grand Prix at Circuit de Barcelona-Catalunya",
                "montreal": "Canadian Grand Prix at Circuit Gilles Villeneuve",
                "lusail": "Qatar Grand Prix at Losail International Circuit",
                "yas marina": "Abu Dhabi Grand Prix at Yas Marina Circuit",
                "red bull ring": "Austrian Grand Prix at Red Bull Ring",
                "spielberg": "Austrian Grand Prix at Red Bull Ring",
            }
            enriched_query = query
            for alias, full_name in ALIAS_MAP.items():
                if alias.lower() in query.lower():
                    enriched_query = f"{query} (Note: '{alias}' refers to the {full_name})"
                    break

            # Step 1: Determine if input is raw SQL or natural language
            stripped = query.strip()
            if stripped.upper().startswith("SELECT"):
                # Agent already generated SQL — use directly (no LLM call!)
                sql = stripped
            else:
                # Fallback: LLM generates SQL from natural language
                user_prompt = f"User question: {enriched_query}"
                sql = generate_llm_response(SCHEMA_PROMPT, user_prompt)

            if sql.startswith("```"):
                sql = "\n".join(sql.split("\n")[1:-1])
            sql = sql.strip()
            print(f"  [query_data] SQL: {sql}")

            sql_upper = sql.upper().strip()
            # Block non-SELECT statements
            if not sql_upper.startswith("SELECT"):
                return f"ERROR: Only SELECT queries are permitted.\nGenerated SQL: {sql}"
            # Block stacked statements (e.g. SELECT 1; DROP TABLE)
            if re.search(r';\s*\S', sql):
                return f"ERROR: Multi-statement SQL is not permitted.\nGenerated SQL: {sql}"
            # Block forbidden keywords anywhere in the query
            FORBIDDEN = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE", "EXEC"]
            if any(kw in sql_upper for kw in FORBIDDEN):
                return f"ERROR: SQL contains a forbidden keyword.\nGenerated SQL: {sql}"

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
            for idx, row in enumerate(rows[:25], start=1):  # Cap at 25 rows
                formatted = [f"{v:.2f}" if isinstance(v, float) else str(v) for v in row]
                result_lines.append(f"Row {idx}: " + " | ".join(formatted))

            return f"SQL: {sql}\nResults ({len(rows)} rows):\n" + "\n".join(result_lines)

        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
