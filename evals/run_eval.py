"""Eval: 10 analyst questions with expected facts, scored by strict, simple rules.

Run (needs ANTHROPIC_API_KEY and a built database):  python -m evals.run_eval

Every case has the same fields; unused ones are empty. A question passes when ALL of these hold:
  - the status is "answered"
  - each group in `must_contain` has at least one of its phrases in the answer (numbers match as whole
    numbers, commas ignored: "5" is not found in "145")
  - `must_contain_any` (if given) has at least one phrase in the answer, and no `must_not_contain` phrase is there
  - every clause in `must_cite` was retrieved AND cited
  - there are no grounding warnings (unless `allow_warnings`)
"""
import json
import re
import time
from pathlib import Path

from backend import assistant, config, tools

ROOT = Path(__file__).resolve().parent.parent


def norm(text):
    return text.lower().replace(",", "")


def found(phrase, answer):
    """A number must match as a whole number: "5" is not found in "145" or "0.5"."""
    p = norm(phrase)
    if re.fullmatch(r"[\d.]+", p):
        return re.search(rf"(?<![\d.]){re.escape(p)}(?!\d)", answer) is not None
    return p in answer


def score(case, result):
    answer, fails = norm(result["answer"]), []
    if result["status"] != "answered":
        fails.append(f"status is {result['status']}")
    for group in case["must_contain"]:
        if not any(found(p, answer) for p in group):
            fails.append(f"answer has none of {group}")
    if case["must_contain_any"] and not any(found(p, answer) for p in case["must_contain_any"]):
        fails.append(f"answer has none of {case['must_contain_any']}")
    fails += [f"answer must not contain '{p}'" for p in case["must_not_contain"] if found(p, answer)]
    cited = {s["id"] for s in result["policy_sections"] if s["cited"]}
    fails += [f"clause {c} not retrieved and cited" for c in case["must_cite"] if c not in cited]
    if result["warnings"] and not case["allow_warnings"]:
        fails.append(f"grounding warnings: {result['warnings']}")
    return fails


def main():
    cases = json.loads((ROOT / "evals" / "eval_set.json").read_text(encoding="utf-8"))
    index, rows = tools.PolicyIndex(config.POLICY_PATH), []
    for case in cases:
        started = time.monotonic()
        try:
            result = assistant.answer_question(case["question"], index)
            fails, answer = score(case, result), result["answer"]
        except assistant.AssistantError as e:
            fails, answer = [f"{e.code}: {e.message}"], ""
        rows.append(dict(id=case["id"], question=case["question"], passed=not fails, failures=fails,
                         answer=answer, seconds=round(time.monotonic() - started, 1)))
        print(f"{'PASS' if not fails else 'FAIL'}  {case['id']}  {case['question'][:70]}")
        for f in fails:
            print(f"      - {f}")
    passed = sum(r["passed"] for r in rows)
    print(f"\nPassed {passed}/{len(rows)}")
    out = ROOT / "output" / "eval_results.json"
    out.write_text(json.dumps(dict(passed=passed, total=len(rows), cases=rows), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
