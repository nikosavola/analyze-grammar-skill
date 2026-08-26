#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run evals/evals.json against SKILL.md using the Antigravity CLI (agy), authenticated via GEMINI_API_KEY.

For each selected eval case, runs `agy` once with SKILL.md's content injected into the prompt, capturing the full
turn-by-turn stream (not just the final text) so the grader can see whether a tool was actually invoked. A second
`agy` call, constrained to a JSON schema, grades that transcript against the case's `expectations`. The pass rate
is computed here from the grader's per-expectation booleans, not trusted from the model's own summary. A case that
errors or fails to grade counts as 0.0, so a flaky backend can't silently inflate the score. Exits non-zero if the
mean pass rate across selected cases is below --fail-under, so this can gate CI.

Requires `agy` on PATH, authenticated for headless use: a $HOME/.gemini/antigravity-cli/settings.json containing
{"modelProvider": "gemini"} and a GEMINI_API_KEY environment variable. See .github/workflows/eval-skill.yml.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "SKILL.md"
EVALS_JSON = REPO_ROOT / "evals" / "evals.json"

# Verified against a fresh $HOME with only {"modelProvider": "gemini"} + GEMINI_API_KEY set (no cached OAuth
# credentials) - this is the exact auth mode CI uses, and other model labels ("Gemini 3.5 Flash (Medium)", the
# GCP-project-auth form) are rejected under it.
DEFAULT_MODEL = "Gemini 3.5 Flash"
DEFAULT_CASES = [1, 2, 5, 7]
SUBPROCESS_TIMEOUT_SECONDS = 260
PRINT_TIMEOUT = "240s"

GRADING_SCHEMA = {
    "type": "object",
    "required": ["expectations"],
    "properties": {
        "expectations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "passed", "evidence"],
                "properties": {
                    "text": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
            },
        },
    },
}


def run_agy(prompt: str, *, model: str) -> dict:
    """Run one headless agy turn with a full stream-json transcript and return {status, response, transcript}."""
    try:
        result = subprocess.run(  # noqa: S603
            [
                "agy",
                "-p",
                prompt,
                "--model",
                model,
                "--output-format",
                "stream-json",
                "--dangerously-skip-permissions",
                "--print-timeout",
                PRINT_TIMEOUT,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "error": "subprocess timed out", "response": "", "transcript": ""}

    events = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    final = next((e["result"] for e in reversed(events) if e.get("event") == "result"), None)
    if final is None:
        return {
            "status": "ERROR",
            "error": f"no result event in agy output: stdout={result.stdout!r} stderr={result.stderr!r}",
            "response": "",
            "transcript": "",
        }

    # Give the grader the raw step_update stream, not just the final text, so it can see whether a tool
    # (e.g. running scripts/analyze_grammar.py) was actually invoked rather than guessing from prose.
    transcript = "\n".join(json.dumps(e) for e in events if e.get("event") == "step_update")
    return {**final, "transcript": transcript}


def run_agy_json_schema(prompt: str, schema: dict, *, model: str) -> dict | None:
    """Run one headless, tool-restricted agy turn constrained to a JSON schema; return the parsed inner response."""
    try:
        result = subprocess.run(  # noqa: S603
            [
                "agy",
                "-p",
                prompt,
                "--model",
                model,
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema),
                # The prompt embeds the executor's response, which can include live Wiktionary content the
                # grader doesn't control; --sandbox limits what an injected instruction could do even though
                # this text-in/JSON-out task shouldn't need tools at all.
                "--dangerously-skip-permissions",
                "--sandbox",
                "--print-timeout",
                PRINT_TIMEOUT,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("::warning::grader subprocess timed out", file=sys.stderr)
        return None

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"::warning::grader envelope unparseable: {result.stdout!r} {result.stderr!r}", file=sys.stderr)
        return None
    if envelope.get("status") != "SUCCESS":
        print(f"::warning::grader run failed: {envelope.get('error')}", file=sys.stderr)
        return None
    try:
        return json.loads(envelope["response"])
    except json.JSONDecodeError:
        print(f"::warning::grader response wasn't valid JSON: {envelope.get('response')!r}", file=sys.stderr)
        return None


def with_skill_prompt(skill_md: str, task_prompt: str) -> str:
    return (
        "You have access to the following Agent Skill. Follow its instructions if and only if they are relevant "
        f"to the request below.\n\n--- SKILL.md ---\n{skill_md}\n--- end SKILL.md ---\n\nUser request: {task_prompt}"
    )


def grading_prompt(case: dict, response_text: str, transcript: str) -> str:
    expectations = "\n".join(f"- {e}" for e in case["expectations"])
    return (
        "Grade an AI agent's turn against a list of expectations, using both its final response and the raw "
        "step-by-step transcript below (the transcript shows whether tools were actually called, which the "
        "final response text alone may not reveal). For each expectation, decide PASS or FAIL with specific "
        "evidence; a plausible-sounding response that doesn't actually demonstrate the expectation is a FAIL. "
        "Return exactly one entry per expectation listed below, in the same order, with its exact text.\n\n"
        f"Task given to the agent: {case['prompt']}\n\n"
        f"Expected outcome: {case['expected_output']}\n\n"
        f"Expectations to check:\n{expectations}\n\n"
        f"Agent's final response:\n{response_text}\n\n"
        f"Raw turn-by-turn transcript (JSON lines):\n{transcript}\n\n"
        "Return JSON matching the required schema exactly."
    )


def score(case: dict, grading: dict | None) -> tuple[float, list[dict]]:
    """Compute the pass rate ourselves from the grader's per-expectation booleans; never trust a self-reported rate."""
    if grading is None:
        return 0.0, []
    exps = grading.get("expectations", [])
    if len(exps) != len(case["expectations"]):
        print(
            f"::warning::eval {case['id']}: grader returned {len(exps)} expectations, "
            f"expected {len(case['expectations'])}; treating as failed",
            file=sys.stderr,
        )
        return 0.0, []
    passed = sum(1 for e in exps if e.get("passed") is True)
    return (passed / len(exps) if exps else 0.0), exps


def run_case(case: dict, *, model: str) -> dict:
    skill_md = SKILL_MD.read_text()
    executor = run_agy(with_skill_prompt(skill_md, case["prompt"]), model=model)

    grading = None
    if executor.get("status") == "SUCCESS":
        prompt = grading_prompt(case, executor.get("response", ""), executor.get("transcript", ""))
        grading = run_agy_json_schema(prompt, GRADING_SCHEMA, model=model)

    pass_rate, expectations = score(case, grading)
    return {
        "eval_id": case["id"],
        "prompt": case["prompt"],
        "executor": executor,
        "pass_rate": pass_rate,
        "expectations": expectations,
    }


def parse_case_ids(raw: str, evals: list[dict]) -> list[dict]:
    if raw.strip() == "all":
        return evals
    known_ids = {e["id"] for e in evals}
    wanted_ids = {int(tok) for tok in raw.split(",") if tok.strip()}
    unknown = wanted_ids - known_ids
    if unknown:
        print(f"error: unknown eval id(s) in --cases: {sorted(unknown)}", file=sys.stderr)
        sys.exit(2)
    return [e for e in evals if e["id"] in wanted_ids]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"agy model label (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--cases",
        default=",".join(str(c) for c in DEFAULT_CASES),
        help=f"comma-separated eval ids to run, or 'all' (default: {DEFAULT_CASES})",
    )
    parser.add_argument("--fail-under", type=float, default=0.7, help="minimum mean pass rate to succeed")
    args = parser.parse_args()

    evals = json.loads(EVALS_JSON.read_text())["evals"]
    selected = parse_case_ids(args.cases, evals)

    # Fail fast on the first case rather than burning the whole (rate-limited) run on a bad --model or auth
    # setup: a rejected model name or auth failure will fail identically for every subsequent case.
    first = run_case(selected[0], model=args.model)
    results = [first]
    if first["executor"].get("status") != "SUCCESS":
        print(
            f"error: first eval case failed to execute, aborting rest of the run: "
            f"{first['executor'].get('error')}",
            file=sys.stderr,
        )
    else:
        results += [run_case(case, model=args.model) for case in selected[1:]]

    summary_lines = ["# analyze-grammar eval results (agy / Gemini)", ""]
    for r in results:
        summary_lines.append(f"## Eval {r['eval_id']}: {r['prompt'][:80]}")
        summary_lines.append(f"- executor status: `{r['executor'].get('status')}`")
        summary_lines.append(f"- pass rate: {r['pass_rate']:.0%}")
        for exp in r["expectations"]:
            mark = "PASS" if exp.get("passed") else "FAIL"
            summary_lines.append(f"  - [{mark}] {exp.get('text')} — {exp.get('evidence')}")
        summary_lines.append("")

    overall = mean(r["pass_rate"] for r in results) if results else 0.0
    summary_lines.append(f"**Overall pass rate: {overall:.0%}** (threshold: {args.fail_under:.0%})")

    summary_md = "\n".join(summary_lines)
    print(summary_md)

    step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        with Path(step_summary_path).open("a") as f:
            f.write(summary_md + "\n")

    if len(results) < len(selected) or overall < args.fail_under:
        print(f"\nFAIL: overall pass rate {overall:.0%} below threshold {args.fail_under:.0%}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
