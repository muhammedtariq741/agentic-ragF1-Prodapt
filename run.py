#!/usr/bin/env python3
"""
F1 Agentic RAG — One-Click Setup & Launcher
============================================
Run this script from a fresh clone to set up everything automatically:

    python3 run.py

It will:
  1. Create a virtual environment (if needed)
  2. Install all dependencies
  3. Ask for your API keys (Groq + Tavily)
  4. Offer to ingest your own external documents
  5. Build the ChromaDB vector store
  6. Launch the interactive F1 agent

No manual steps required.
"""

import os
import sys
import subprocess
import shutil
import glob
import textwrap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(PROJECT_DIR, "venv")
DOCS_DIR = os.path.join(PROJECT_DIR, "data", "documents")
VECTOR_STORE_DIR = os.path.join(PROJECT_DIR, "data", "vector_store")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")
ENV_EXAMPLE = os.path.join(PROJECT_DIR, ".env.example")
REQUIREMENTS = os.path.join(PROJECT_DIR, "requirements.txt")

# Platform-aware venv python path
if sys.platform == "win32":
    VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe")
    VENV_PIP = os.path.join(VENV_DIR, "Scripts", "pip.exe")
else:
    VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python3")
    VENV_PIP = os.path.join(VENV_DIR, "bin", "pip")


# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
CHECK = f"{GREEN}✓{RESET}"
CROSS = f"{RED}✗{RESET}"


def banner(text: str):
    """Print a styled section banner."""
    width = 60
    print(f"\n{CYAN}{'═' * width}")
    print(f"  {BOLD}{text}{RESET}")
    print(f"{CYAN}{'═' * width}{RESET}\n")


def step(msg: str):
    """Print a step indicator."""
    print(f"  {YELLOW}▸{RESET} {msg}")


def success(msg: str):
    """Print a success message."""
    print(f"  {CHECK} {msg}")


def error(msg: str):
    """Print an error message."""
    print(f"  {CROSS} {msg}")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question with a default."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"  {prompt} {suffix}: ").strip().lower()
        if answer == "":
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print(f"  Please answer 'y' or 'n'.")


# ---------------------------------------------------------------------------
# Phase 1: Virtual Environment
# ---------------------------------------------------------------------------
def setup_venv():
    """Create a virtual environment if one doesn't exist."""
    banner("Phase 1 — Virtual Environment")

    if os.path.exists(VENV_PYTHON):
        success("Virtual environment already exists.")
        return

    step("Creating virtual environment...")
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", VENV_DIR],
            check=True, capture_output=True, text=True
        )
        success("Virtual environment created.")
    except subprocess.CalledProcessError as e:
        error(f"Failed to create venv: {e.stderr}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Phase 2: Dependencies
# ---------------------------------------------------------------------------
def install_dependencies():
    """Install all pip dependencies from requirements.txt."""
    banner("Phase 2 — Installing Dependencies")

    step("Installing packages from requirements.txt...")
    try:
        result = subprocess.run(
            [VENV_PIP, "install", "-r", REQUIREMENTS, "-q"],
            check=True, capture_output=True, text=True,
            cwd=PROJECT_DIR
        )
        success("All dependencies installed.")
    except subprocess.CalledProcessError as e:
        error(f"pip install failed:\n{e.stderr}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Phase 3: API Keys
# ---------------------------------------------------------------------------
def setup_api_keys():
    """Prompt user for API keys and write .env file."""
    banner("Phase 3 — API Key Configuration")

    # Check if .env already has real keys
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            content = f.read()
        has_groq = "GROQ_API_KEY=" in content and "your_" not in content.split("GROQ_API_KEY=")[1].split("\n")[0]
        has_tavily = "TAVILY_API_KEY=" in content and "your_" not in content.split("TAVILY_API_KEY=")[1].split("\n")[0]

        if has_groq and has_tavily:
            success(".env file found with API keys configured.")
            if not ask_yes_no("Reconfigure API keys?", default=False):
                return

    print(textwrap.dedent(f"""
    {BOLD}You need two free API keys:{RESET}

      1. {CYAN}Groq{RESET}   → https://console.groq.com  (LLM inference)
      2. {CYAN}Tavily{RESET} → https://tavily.com         (Web search)

    Both are free and take 30 seconds to create.
    """))

    # Groq key
    while True:
        groq_key = input(f"  Enter your {BOLD}Groq API key{RESET} (starts with gsk_): ").strip()
        if groq_key.startswith("gsk_") and len(groq_key) > 20:
            break
        print(f"  {RED}Invalid key. Groq keys start with 'gsk_' and are ~50 characters.{RESET}")

    # Tavily key
    while True:
        tavily_key = input(f"  Enter your {BOLD}Tavily API key{RESET} (starts with tvly-): ").strip()
        if tavily_key.startswith("tvly-") and len(tavily_key) > 10:
            break
        print(f"  {RED}Invalid key. Tavily keys start with 'tvly-'.{RESET}")

    # Write .env
    env_content = f"""GROQ_API_KEY={groq_key}
TAVILY_API_KEY={tavily_key}
"""
    with open(ENV_FILE, "w") as f:
        f.write(env_content)

    success(f".env file written with both API keys.")


# ---------------------------------------------------------------------------
# Phase 4: External Document Ingestion
# ---------------------------------------------------------------------------
def ingest_external_documents():
    """Optionally ingest user-provided documents into the corpus."""
    banner("Phase 4 — Document Ingestion")

    existing_docs = glob.glob(os.path.join(DOCS_DIR, "*.txt"))
    print(f"  Current corpus: {BOLD}{len(existing_docs)} documents{RESET} in data/documents/\n")

    if not ask_yes_no("Do you have external documents (.txt files) you want to add?", default=False):
        success("Skipping external document ingestion.")
        return

    print(textwrap.dedent(f"""
    {BOLD}Provide a path to a folder or a single .txt file.{RESET}
    Supported: .txt files (plain text, race reports, articles, etc.)
    The files will be copied into the project's data/documents/ folder.
    """))

    while True:
        doc_path = input("  Path to folder or file: ").strip().strip("'\"")

        if not os.path.exists(doc_path):
            error(f"Path not found: {doc_path}")
            if not ask_yes_no("Try again?"):
                return
            continue

        # Collect all .txt files
        files_to_add = []
        if os.path.isfile(doc_path):
            if doc_path.endswith(".txt"):
                files_to_add.append(doc_path)
            else:
                error("Only .txt files are supported.")
                continue
        elif os.path.isdir(doc_path):
            files_to_add = glob.glob(os.path.join(doc_path, "*.txt"))
            if not files_to_add:
                error(f"No .txt files found in {doc_path}")
                continue

        # Copy files
        os.makedirs(DOCS_DIR, exist_ok=True)
        added = 0
        for fpath in files_to_add:
            dest = os.path.join(DOCS_DIR, os.path.basename(fpath))
            if os.path.exists(dest):
                step(f"Skipping (already exists): {os.path.basename(fpath)}")
                continue
            shutil.copy2(fpath, dest)
            step(f"Added: {os.path.basename(fpath)}")
            added += 1

        success(f"Added {added} new document(s) to the corpus.")

        if not ask_yes_no("Add more documents from another location?", default=False):
            break

    return True  # Signal that we need to rebuild the vector store


# ---------------------------------------------------------------------------
# Phase 5: Build Vector Store
# ---------------------------------------------------------------------------
def build_vector_store(force_rebuild: bool = False):
    """Build or rebuild the ChromaDB vector store."""
    banner("Phase 5 — Building Vector Store")

    if os.path.exists(VECTOR_STORE_DIR) and not force_rebuild:
        success("Vector store already exists.")
        if not ask_yes_no("Rebuild from scratch?", default=False):
            return
    
    step("Embedding documents into ChromaDB (this may take 30-60 seconds)...")

    try:
        result = subprocess.run(
            [VENV_PYTHON, os.path.join(PROJECT_DIR, "indexing", "embed_docs.py")],
            check=True, capture_output=True, text=True,
            cwd=PROJECT_DIR
        )
        # Print embed_docs output
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line}")
        success("Vector store built successfully.")
    except subprocess.CalledProcessError as e:
        error(f"Embedding failed:\n{e.stderr}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Phase 6: Launch Agent
# ---------------------------------------------------------------------------
def launch_agent():
    """Launch the interactive F1 agent."""
    banner("Phase 6 — Launching F1 Agent")

    print(textwrap.dedent(f"""
    {GREEN}{BOLD}Setup complete! Launching the F1 Agentic RAG system...{RESET}

    {BOLD}Tips:{RESET}
      • Ask about 2024-2025 F1 seasons (stats, strategies, race reports)
      • The agent uses 3 tools: query_data, search_docs, web_search
      • Type 'quit' or 'exit' to stop
      • Max 8 tool calls per question
    """))

    # Hand off to main.py — use os.execv to replace this process
    os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.join(PROJECT_DIR, "main.py")])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.chdir(PROJECT_DIR)

    print(f"""
{CYAN}{'═' * 60}
{BOLD}  🏎️  F1 Agentic RAG — One-Click Setup & Launcher
{'═' * 60}{RESET}

  This script will set up and launch the F1 Agentic RAG system.
  Everything is automated — just follow the prompts.
    """)

    # Phase 1-3: Core setup
    setup_venv()
    install_dependencies()
    setup_api_keys()

    # Phase 4: Optional external docs
    docs_added = ingest_external_documents()

    # Phase 5: Vector store
    build_vector_store(force_rebuild=bool(docs_added))

    # Phase 6: Launch
    launch_agent()


if __name__ == "__main__":
    main()
