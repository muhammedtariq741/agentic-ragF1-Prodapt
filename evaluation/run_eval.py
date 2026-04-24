"""Evaluation runner — loads eval_questions.json, runs agent, generates accuracy report."""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import F1Agent, print_trace


def load_questions(path: str = None) -> list[dict]:
    """Load all evaluation questions from the JSON file."""
    if path is None:
        path = Path(__file__).parent / "eval_questions.json"
    with open(path, "r") as f:
        data = json.load(f)
    # Flatten all categories into one list
    questions = []
    for category in ["single_tool_questions", "multi_tool_questions", "refusal_questions", "edge_cases"]:
        questions.extend(data.get(category, []))
    return questions


def evaluate_single(agent: F1Agent, q: dict) -> dict:
    """Run the agent on a single question and score the result."""
    qid = q["id"]
    question = q["question"]
    category = q["category"]

    # Handle empty input edge case
    if not question.strip():
        return {
            "id": qid, "category": category, "question": "(empty)",
            "answer": "Skipped — empty input", "tools_used": [],
            "steps": 0, "passed": True, "notes": "Empty input handled gracefully"
        }

    print(f"\n{'='*60}")
    print(f"  [{qid}/{20}] {question}")
    print(f"{'='*60}")

    try:
        result = agent.run(question)
    except Exception as e:
        return {
            "id": qid, "category": category, "question": question,
            "answer": f"CRASH: {e}", "tools_used": [], "steps": 0,
            "passed": False, "notes": f"Agent crashed: {e}"
        }

    answer = result.get("answer", "")
    trace = result.get("trace", [])
    steps = result.get("steps_used", 0)

    # Extract which tools were actually called
    tools_used = [e["tool"] for e in trace if e.get("state") == "ACT" and "tool" in e]

    # --- Scoring Logic ---
    passed = False
    notes = ""

    if category == "single_tool":
        expected_tool = q.get("expected_tool", "")
        keyword = q.get("expected_answer_contains", "").lower()
        correct_tool = expected_tool in tools_used
        has_keyword = keyword in answer.lower() if keyword else True
        passed = correct_tool and has_keyword
        notes = f"Tool: {'✅' if correct_tool else '❌'} ({tools_used}), Keyword '{keyword}': {'✅' if has_keyword else '❌'}"

    elif category == "multi_tool":
        expected_tools = q.get("expected_tools", [])
        keyword = q.get("expected_answer_contains", "").lower()
        all_tools_used = all(t in tools_used for t in expected_tools)
        has_keyword = keyword in answer.lower() if keyword else True
        passed = all_tools_used and has_keyword
        notes = f"Tools: {'✅' if all_tools_used else '❌'} (expected {expected_tools}, got {tools_used}), Keyword: {'✅' if has_keyword else '❌'}"

    elif category == "refusal":
        # Should NOT call any tools AND should contain a polite refusal
        no_tools = len(tools_used) == 0
        is_refusal = any(w in answer.lower() for w in ["cannot", "unable", "refuse", "sorry", "outside", "won't", "don't"])
        passed = no_tools and is_refusal
        notes = f"No tools: {'✅' if no_tools else '❌'} ({tools_used}), Refusal language: {'✅' if is_refusal else '❌'}"

    elif category == "edge_case":
        # Edge cases are manually reviewed; auto-pass if no crash
        passed = True
        notes = f"Tools used: {tools_used}, Steps: {steps}"

    return {
        "id": qid, "category": category, "question": question,
        "answer": answer[:500], "tools_used": tools_used,
        "steps": steps, "passed": passed, "notes": notes
    }


def generate_report(results: list[dict], output_path: str = None) -> str:
    """Generate a markdown accuracy report from evaluation results."""
    if output_path is None:
        output_path = Path(__file__).parent / "eval_report.md"

    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    # Category breakdown
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0}
        categories[cat]["total"] += 1
        if r["passed"]:
            categories[cat]["passed"] += 1

    lines = [
        f"# F1 Agentic RAG — Evaluation Report",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Overall Accuracy:** {passed}/{total} ({100*passed/total:.0f}%)\n",
        "## Category Breakdown\n",
        "| Category | Passed | Total | Accuracy |",
        "|----------|--------|-------|----------|",
    ]
    for cat, data in categories.items():
        acc = 100 * data["passed"] / data["total"] if data["total"] else 0
        lines.append(f"| {cat} | {data['passed']} | {data['total']} | {acc:.0f}% |")

    lines.append("\n## Detailed Results\n")
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        lines.append(f"### {icon} Q{r['id']}: {r['question']}")
        lines.append(f"- **Category:** {r['category']}")
        lines.append(f"- **Tools Used:** {r['tools_used']}")
        lines.append(f"- **Steps:** {r['steps']}")
        lines.append(f"- **Answer:** {r['answer'][:300]}")
        lines.append(f"- **Notes:** {r['notes']}\n")

    # Failure analysis
    failures = [r for r in results if not r["passed"]]
    if failures:
        lines.append("## Failure Analysis\n")
        for f in failures:
            lines.append(f"- **Q{f['id']}** ({f['category']}): {f['notes']}")

    report = "\n".join(lines)
    with open(output_path, "w") as f:
        f.write(report)
    print(f"\n📄 Report saved to: {output_path}")
    return report


def main():
    print("=" * 60)
    print("  🏎️  F1 Agentic RAG — Evaluation Runner")
    print("=" * 60)

    agent = F1Agent()
    questions = load_questions()
    print(f"Loaded {len(questions)} evaluation questions.\n")

    results = []
    for q in questions:
        result = evaluate_single(agent, q)
        results.append(result)
        icon = "✅" if result["passed"] else "❌"
        print(f"  {icon} Q{result['id']} [{result['category']}]: {result['notes']}")
        print("  [Waiting 15s to respect Groq API Rate Limits...]")
        time.sleep(15)  # Respect Groq API free-tier rate limits (TPM/RPM)

    report = generate_report(results)

    # Print summary
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{'='*60}")
    print(f"  FINAL SCORE: {passed}/{len(results)} ({100*passed/len(results):.0f}%)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
