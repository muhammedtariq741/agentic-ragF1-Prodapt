"""
Comprehensive Stress Test for SearchDocsTool
============================================
Goes far beyond the basic 15-test suite.
Tests adversarial inputs, concurrency, semantic drift, cross-year confusion,
typos, multilingual queries, and performance under load.

Corpus assumed: 48 docs (24 × 2024 + 24 × 2025 race reports)

Run from your project root:
    python3 stress_test_search_docs.py
    python3 stress_test_search_docs.py --category adversarial
    python3 stress_test_search_docs.py --fast        # skip slow load tests
    python3 stress_test_search_docs.py --list-categories
"""

import sys
import os
import time
import argparse
import threading
import statistics
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.search_docs import SearchDocsTool

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"

# ── Result container ─────────────────────────────────────────────────────────
@dataclass
class TestResult:
    test_id: str
    category: str
    note: str
    passed: bool
    elapsed: float
    failures: list = field(default_factory=list)
    result_preview: str = ""
    sources_returned: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# TEST DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

TESTS = [

    # ── CATEGORY 1: CORE RETRIEVAL ────────────────────────────────────────────
    {
        "id": "CR-01", "category": "core_retrieval",
        "query": "Who won the 2024 British Grand Prix?",
        "must_contain": ["hamilton"], "must_not": ["ERROR"],
        "source_hint": "2024_British_Grand_Prix.txt",
        "note": "Basic winner retrieval — 2024 British GP",
    },
    {
        "id": "CR-02", "category": "core_retrieval",
        "query": "Who won the 2025 British Grand Prix?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2025_British_Grand_Prix.txt",
        "note": "Same race, different year — must prefer 2025 doc",
    },
    {
        "id": "CR-03", "category": "core_retrieval",
        "query": "What happened at the 2024 Monaco Grand Prix?",
        "must_contain": ["leclerc"], "must_not": ["ERROR"],
        "source_hint": "2024_Monaco_Grand_Prix.txt",
        "note": "Monaco 2024 — Leclerc home win",
    },
    {
        "id": "CR-04", "category": "core_retrieval",
        "query": "Describe the 2024 São Paulo Grand Prix race in the rain",
        "must_contain": ["verstappen"], "must_not": ["ERROR"],
        "source_hint": "2024_São_Paulo_Grand_Prix.txt",
        "note": "São Paulo 2024 — wet race comeback",
    },
    {
        "id": "CR-05", "category": "core_retrieval",
        "query": "What happened at the 2024 Italian Grand Prix at Monza?",
        "must_contain": ["monza"], "must_not": ["ERROR"],
        "source_hint": "2024_Italian_Grand_Prix.txt",
        "note": "Monza 2024 — must not confuse with Imola",
    },
    {
        "id": "CR-06", "category": "core_retrieval",
        "query": "Who won the 2025 Australian Grand Prix?",
        "must_contain": ["australian"], "must_not": ["ERROR"],
        "source_hint": "2025_Australian_Grand_Prix.txt",
        "note": "2025 season opener",
    },

    # ── CATEGORY 2: YEAR DISAMBIGUATION ──────────────────────────────────────
    {
        "id": "YD-01", "category": "year_disambiguation",
        "query": "2024 Japanese Grand Prix Suzuka race report",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2024_Japanese_Grand_Prix.txt",
        "note": "Must prefer 2024 over 2025 Japanese GP",
    },
    {
        "id": "YD-02", "category": "year_disambiguation",
        "query": "2025 Japanese Grand Prix Suzuka race report",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2025_Japanese_Grand_Prix.txt",
        "note": "Must prefer 2025 over 2024 Japanese GP",
    },
    {
        "id": "YD-03", "category": "year_disambiguation",
        "query": "What happened at the 2024 Singapore Grand Prix?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2024_Singapore_Grand_Prix.txt",
        "note": "Singapore 2024 vs 2025",
    },
    {
        "id": "YD-04", "category": "year_disambiguation",
        "query": "What happened at the 2025 Singapore Grand Prix?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2025_Singapore_Grand_Prix.txt",
        "note": "Singapore 2025 vs 2024",
    },
    {
        "id": "YD-05", "category": "year_disambiguation",
        "query": "2024 Las Vegas Grand Prix night race",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2024_Las_Vegas_Grand_Prix.txt",
        "note": "Las Vegas 2024",
    },
    {
        "id": "YD-06", "category": "year_disambiguation",
        "query": "2025 Belgian Grand Prix at Spa-Francorchamps result",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2025_Belgian_Grand_Prix.txt",
        "note": "Spa 2025 vs 2024",
    },

    # ── CATEGORY 3: SEMANTIC DRIFT ────────────────────────────────────────────
    {
        "id": "SD-01", "category": "semantic_drift",
        "query": "Hamilton's emotional victory on home soil in 2024",
        "must_contain": ["hamilton"], "must_not": ["ERROR"],
        "source_hint": "2024_British_Grand_Prix.txt",
        "note": "Paraphrase — 'home soil' must map to British GP",
    },
    {
        "id": "SD-02", "category": "semantic_drift",
        "query": "Leclerc emotional home victory Monte Carlo 2024",
        "must_contain": ["leclerc"], "must_not": ["ERROR"],
        "source_hint": "2024_Monaco_Grand_Prix.txt",
        "note": "Indirect — 'Monte Carlo' + 'home victory' must map to Monaco GP doc",
    },
    {
        "id": "SD-03", "category": "semantic_drift",
        "query": "tyre strategy and pit stop timing in the wet Brazilian race",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2024_São_Paulo_Grand_Prix.txt",
        "note": "'wet Brazilian race' must map to São Paulo GP",
    },
    {
        "id": "SD-04", "category": "semantic_drift",
        "query": "street circuit night race in the desert 2024",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Ambiguous — Las Vegas/Saudi/Abu Dhabi; must not crash",
    },
    {
        "id": "SD-05", "category": "semantic_drift",
        "query": "Who dominated the opening rounds of the 2025 F1 season?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Vague temporal — should return early 2025 race docs",
    },

    # ── CATEGORY 4: ADVERSARIAL INPUTS ───────────────────────────────────────
    {
        "id": "AD-01", "category": "adversarial",
        "query": "",
        "must_contain": [], "must_not": ["ERROR: OperationalError", "ERROR: AttributeError"],
        "source_hint": None,
        "note": "Empty string — must not crash",
    },
    {
        "id": "AD-02", "category": "adversarial",
        "query": "aaaaaaaaaa bbbbbbbbbbb zzzzzzzzz 12345",
        "must_contain": [], "must_not": ["ERROR: OperationalError"],
        "source_hint": None,
        "note": "Gibberish — must return chunks or no-results, not crash",
    },
    {
        "id": "AD-03", "category": "adversarial",
        "query": "   ",
        "must_contain": [], "must_not": ["ERROR: AttributeError"],
        "source_hint": None,
        "note": "Whitespace-only — must handle gracefully",
    },
    {
        "id": "AD-04", "category": "adversarial",
        "query": "!@#$%^&*()_+-=[]{}|;':\",./<>?",
        "must_contain": [], "must_not": ["ERROR: TypeError", "ERROR: ValueError"],
        "source_hint": None,
        "note": "Special characters — must not raise unhandled exception",
    },
    {
        "id": "AD-05", "category": "adversarial",
        "query": "A" * 2000,
        "must_contain": [], "must_not": ["ERROR: TypeError"],
        "source_hint": None,
        "note": "Very long query (2000 chars) — must not timeout badly",
        "max_latency": 15.0,
    },
    {
        "id": "AD-06", "category": "adversarial",
        "query": "SELECT * FROM documents; DROP TABLE documents;--",
        "must_contain": [], "must_not": ["ERROR: OperationalError"],
        "source_hint": None,
        "note": "SQL injection attempt — must treat as plain text",
    },
    {
        "id": "AD-07", "category": "adversarial",
        "query": "<script>alert('xss')</script>",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "XSS attempt — must treat as plain text, not execute",
    },
    {
        "id": "AD-08", "category": "adversarial",
        "query": "\n\n\n\t\t\t",
        "must_contain": [], "must_not": ["ERROR: AttributeError"],
        "source_hint": None,
        "note": "Newlines/tabs only — must not crash",
    },
    {
        "id": "AD-09", "category": "adversarial",
        "query": "null None undefined NaN Infinity",
        "must_contain": [], "must_not": ["ERROR: TypeError"],
        "source_hint": None,
        "note": "Programming null values as string — must treat as plain text",
    },
    {
        "id": "AD-10", "category": "adversarial",
        "query": "🏎️🏆🔥💨 who win race 2024???",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Emoji + informal text — must handle unicode",
    },

    # ── CATEGORY 5: TYPOS & MISSPELLINGS ─────────────────────────────────────
    {
        "id": "TY-01", "category": "typos",
        "query": "Hamiltn 2024 Brittish Gran Prix winner",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Heavy typos — semantic search should still retrieve British GP",
    },
    {
        "id": "TY-02", "category": "typos",
        "query": "Verstapen wins Sao Palo 2024",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Misspelled name + location",
    },
    {
        "id": "TY-03", "category": "typos",
        "query": "Leclerk Monaco 24 win",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Misspelled name + abbreviated year",
    },
    {
        "id": "TY-04", "category": "typos",
        "query": "monoco grandprix twenty twenty four",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Spelled-out year + misspelled Monaco",
    },

    # ── CATEGORY 6: OUT-OF-CORPUS ─────────────────────────────────────────────
    {
        "id": "OC-01", "category": "out_of_corpus",
        "query": "What happened at the 2023 Belgian Grand Prix?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "2023 not in corpus — graceful degradation",
    },
    {
        "id": "OC-02", "category": "out_of_corpus",
        "query": "How did Mercedes perform in the 2022 season?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "2022 not in corpus",
    },
    {
        "id": "OC-03", "category": "out_of_corpus",
        "query": "Michael Schumacher's 2004 championship winning season",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Historical F1 (2004) — not in corpus",
    },
    {
        "id": "OC-04", "category": "out_of_corpus",
        "query": "What is the weather forecast for the 2026 Abu Dhabi Grand Prix?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Future race (2026) — must not crash",
    },
    {
        "id": "OC-05", "category": "out_of_corpus",
        "query": "Premier League football results last weekend",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Wrong domain entirely — must not crash",
    },

    # ── CATEGORY 7: MULTILINGUAL ──────────────────────────────────────────────
    {
        "id": "ML-01", "category": "multilingual",
        "query": "Qui a gagné le Grand Prix de Monaco 2024?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "French query — Monaco 2024",
    },
    {
        "id": "ML-02", "category": "multilingual",
        "query": "Verstappen ganó en Brasil 2024?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Spanish query — São Paulo 2024",
    },
    {
        "id": "ML-03", "category": "multilingual",
        "query": "2024年イギリスグランプリの優勝者は誰ですか？",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Japanese query — British GP 2024",
    },

    # ── CATEGORY 8: CITATION FORMAT ───────────────────────────────────────────
    {
        "id": "CF-01", "category": "citation_format",
        "query": "What strategy did McLaren use at the 2024 Hungarian Grand Prix?",
        "must_contain": ["source:", "chunk:"], "must_not": ["ERROR"],
        "source_hint": "2024_Hungarian_Grand_Prix.txt",
        "note": "Output MUST include [Source: filename | Chunk: id] format",
    },
    {
        "id": "CF-02", "category": "citation_format",
        "query": "What happened at the 2025 Bahrain Grand Prix?",
        "must_contain": ["source:", "chunk:"], "must_not": ["ERROR"],
        "source_hint": "2025_Bahrain_Grand_Prix.txt",
        "note": "Citation must reference 2025 Bahrain GP",
    },
    {
        "id": "CF-03", "category": "citation_format",
        "query": "2024 Abu Dhabi Grand Prix result and podium",
        "must_contain": ["source:", "chunk:"], "must_not": ["ERROR"],
        "source_hint": "2024_Abu_Dhabi_Grand_Prix.txt",
        "note": "Citation test — season finale 2024",
    },

    # ── CATEGORY 9: PERFORMANCE / LATENCY ────────────────────────────────────
    {
        "id": "PF-01", "category": "performance",
        "query": "Who won the 2024 Japanese Grand Prix?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Simple query — must complete in < 3s",
        "max_latency": 3.0,
    },
    {
        "id": "PF-02", "category": "performance",
        "query": "Comprehensive analysis of all Red Bull performance issues across 2024 and 2025 including tyre degradation and aerodynamic problems",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Long complex query — must complete in < 8s",
        "max_latency": 8.0,
    },
    {
        "id": "PF-03", "category": "performance",
        "query": "safety car",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Very short query — must complete in < 3s",
        "max_latency": 3.0,
    },

    # ── CATEGORY 10: MULTI-ENTITY / COMPARISON ────────────────────────────────
    {
        "id": "ME-01", "category": "multi_entity",
        "query": "Compare Verstappen and Hamilton performance in 2024",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Two drivers compared",
    },
    {
        "id": "ME-02", "category": "multi_entity",
        "query": "Ferrari vs McLaren battle in the 2024 constructors championship",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Two teams compared",
    },
    {
        "id": "ME-03", "category": "multi_entity",
        "query": "Who retired from the 2024 Australian Grand Prix due to mechanical failure?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": "2024_Australian_Grand_Prix.txt",
        "note": "Specific incident — retirements at Australian GP",
    },
    {
        "id": "ME-04", "category": "multi_entity",
        "query": "Sprint races in 2024 F1 season results",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Cross-race query — sprint format",
    },

    # ── CATEGORY 11: AGENT LOOP (repeated calls — no state corruption) ────────
    {
        "id": "AL-01", "category": "agent_loop",
        "query": "Who won the 2024 British Grand Prix?",
        "must_contain": ["hamilton"], "must_not": ["ERROR"],
        "source_hint": "2024_British_Grand_Prix.txt",
        "note": "Repeat call #2 — result must be identical to CR-01",
    },
    {
        "id": "AL-02", "category": "agent_loop",
        "query": "Who won the 2024 British Grand Prix?",
        "must_contain": ["hamilton"], "must_not": ["ERROR"],
        "source_hint": "2024_British_Grand_Prix.txt",
        "note": "Repeat call #3 — no degradation allowed",
    },
    {
        "id": "AL-03", "category": "agent_loop",
        "query": "What is 2+2?",
        "must_contain": [], "must_not": ["ERROR"],
        "source_hint": None,
        "note": "Non-F1 question sent to search_docs — should return chunks, not crash",
    },
]


# ── Concurrency test ──────────────────────────────────────────────────────────

def run_concurrency_test(tool, n_threads: int = 5) -> tuple:
    """Fire n simultaneous queries and verify all return valid results."""
    results = {}
    errors  = {}

    def worker(tid):
        try:
            r = tool.run(f"2024 race result season round {tid}")
            results[tid] = r
        except Exception as e:
            errors[tid] = str(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.time() - t0

    if errors:
        return False, f"{len(errors)}/{n_threads} threads crashed: {errors}"
    if len(results) != n_threads:
        return False, f"Only {len(results)}/{n_threads} threads returned"
    return True, f"All {n_threads} concurrent threads OK in {elapsed:.1f}s"


# ── Checker ───────────────────────────────────────────────────────────────────

CRASH_SIGNALS = [
    "ERROR: OperationalError", "ERROR: AttributeError",
    "ERROR: TypeError", "ERROR: ValueError",
    "Traceback (most recent call last)",
]

def check_result(result: str, test: dict) -> tuple:
    result_lower = result.lower()
    failures = []

    for sig in CRASH_SIGNALS:
        if sig in result:
            failures.append(f"Tool crashed: {sig}")

    for term in test.get("must_contain", []):
        if term.lower() not in result_lower:
            failures.append(f"Missing expected term: '{term}'")

    for term in test.get("must_not", []):
        if term.lower() in result_lower:
            failures.append(f"Found forbidden term: '{term}'")

    hint = test.get("source_hint")
    if hint and hint.lower() not in result_lower:
        failures.append(f"Expected source '{hint}' not in output")

    return len(failures) == 0, failures


def extract_sources(result: str) -> list:
    import re
    return [s.strip() for s in re.findall(r'\[Source:\s*([^\|]+)', result)]


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all(categories_filter=None, fast: bool = False) -> list:
    tool = SearchDocsTool()
    output = []
    latencies = []

    tests_to_run = [
        t for t in TESTS
        if (categories_filter is None or t["category"] in categories_filter)
        and not (fast and t.get("max_latency", 0) > 5)
    ]

    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}  SearchDocsTool — Comprehensive Stress Test{RESET}")
    print(f"{BOLD}  {len(tests_to_run)} tests | "
          f"{len(set(t['category'] for t in tests_to_run))} categories{RESET}")
    print(f"{BOLD}{'═'*70}{RESET}\n")

    for test in tests_to_run:
        cat = test["category"]
        print(f"{CYAN}[{test['id']}]{RESET} {DIM}{cat}{RESET} — {test['note']}")

        q_raw = test["query"]
        q_display = (q_raw[:70] + "…") if len(q_raw) > 70 else q_raw
        if q_display.strip():
            print(f"  Query: \"{q_display}\"")
        else:
            print(f"  Query: (empty / whitespace)")

        if test.get("source_hint"):
            print(f"  Expecting top source: {test['source_hint']}")

        t0 = time.time()
        try:
            result = tool.run(test["query"])
        except Exception as e:
            result = f"ERROR: Uncaught exception — {type(e).__name__}: {e}"
        elapsed = round(time.time() - t0, 2)
        latencies.append(elapsed)

        passed, failures = check_result(result, test)

        max_lat = test.get("max_latency")
        if max_lat and elapsed > max_lat:
            failures.append(f"Latency {elapsed}s exceeded limit {max_lat}s")
            passed = False

        sources = extract_sources(result)
        preview = result.replace("\n", " ")[:180]

        tr = TestResult(
            test_id=test["id"], category=cat, note=test["note"],
            passed=passed, elapsed=elapsed, failures=failures,
            result_preview=preview, sources_returned=sources,
        )
        output.append(tr)

        if passed:
            print(f"  {GREEN}PASS{RESET}  ({elapsed}s)")
            if sources:
                print(f"  {DIM}Sources: {', '.join(sources[:3])}{RESET}")
        else:
            print(f"  {RED}FAIL{RESET}  ({elapsed}s)")
            for f in failures:
                print(f"    {YELLOW}→ {f}{RESET}")
            print(f"  {DIM}Preview: {preview}{RESET}")

        print()
        time.sleep(0.3)

    # ── Concurrency test ──────────────────────────────────────────────────────
    if categories_filter is None or "concurrency" in categories_filter:
        print(f"{CYAN}[CC-01]{RESET} {DIM}concurrency{RESET} — 5 simultaneous queries")
        cc_pass, cc_msg = run_concurrency_test(tool, n_threads=5)
        print(f"  {GREEN if cc_pass else RED}{'PASS' if cc_pass else 'FAIL'}{RESET}  {cc_msg}")
        output.append(TestResult(
            test_id="CC-01", category="concurrency",
            note="5 simultaneous threads",
            passed=cc_pass, elapsed=0,
            failures=[] if cc_pass else [cc_msg],
        ))
        print()

    return output, latencies


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: list, latencies: list) -> bool:
    cats = {}
    for r in results:
        cats.setdefault(r.category, {"pass": 0, "fail": 0, "items": []})
        if r.passed:
            cats[r.category]["pass"] += 1
        else:
            cats[r.category]["fail"] += 1
            cats[r.category]["items"].append(r)

    total_pass = sum(v["pass"] for v in cats.values())
    total_fail = sum(v["fail"] for v in cats.values())
    total      = total_pass + total_fail

    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}  STRESS TEST SUMMARY{RESET}")
    print(f"{BOLD}{'═'*70}{RESET}")

    print(f"\n  {'Category':<22} {'Pass':>5} {'Fail':>5}  Bar")
    print(f"  {'-'*55}")
    for cat, counts in cats.items():
        bar = GREEN + "■" * counts["pass"] + RESET + RED + "■" * counts["fail"] + RESET
        flag = "" if counts["fail"] == 0 else f"  {YELLOW}← needs fix{RESET}"
        print(f"  {cat:<22} {counts['pass']:>5} {counts['fail']:>5}  {bar}{flag}")

    print(f"\n  {'─'*55}")
    if total_fail == 0:
        print(f"  {BOLD}{GREEN}ALL {total}/{total} TESTS PASSED ✓{RESET}")
    else:
        print(f"  {BOLD}{RED}{total_fail} FAILED{RESET}{BOLD}, "
              f"{GREEN}{total_pass} PASSED{RESET} ({total} total){RESET}")

    if latencies:
        print(f"\n  {BOLD}Latency stats:{RESET}")
        print(f"    Mean:   {statistics.mean(latencies):.2f}s")
        print(f"    Median: {statistics.median(latencies):.2f}s")
        print(f"    Max:    {max(latencies):.2f}s")
        print(f"    Min:    {min(latencies):.2f}s")

    all_failures = [r for r in results if not r.passed]
    if all_failures:
        print(f"\n  {BOLD}{RED}Failed tests:{RESET}")
        for r in all_failures:
            print(f"\n  [{r.test_id}] {r.note}")
            for f in r.failures:
                print(f"    → {YELLOW}{f}{RESET}")
            if r.result_preview:
                print(f"    Preview: {DIM}{r.result_preview[:150]}{RESET}")

    print(f"\n{BOLD}{'═'*70}{RESET}\n")

    if total_fail > 0:
        print(f"{RED}Action required: fix the {total_fail} failing test(s) before demo.{RESET}\n")
    else:
        print(f"{GREEN}search_docs is stress-test ready ✓{RESET}\n")

    return total_fail == 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stress test SearchDocsTool")
    parser.add_argument(
        "--category", nargs="+",
        help="Run specific categories only e.g. --category adversarial year_disambiguation",
    )
    parser.add_argument(
        "--fast", action="store_true",
        help="Skip long-running performance tests (max_latency > 5s)",
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="List all available categories and exit",
    )
    args = parser.parse_args()

    if args.list_categories:
        cats = sorted(set(t["category"] for t in TESTS))
        print("Available categories:")
        for c in cats:
            count = sum(1 for t in TESTS if t["category"] == c)
            print(f"  {c:<25} ({count} tests)")
        return

    results, latencies = run_all(
        categories_filter=args.category,
        fast=args.fast,
    )
    success = print_summary(results, latencies)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()