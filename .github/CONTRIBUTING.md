# Contributing

## Setup

This project uses [uv](https://docs.astral.sh/uv/). There is no manual virtualenv step: `uv run` creates and syncs
`.venv` from `pyproject.toml` on demand.

```bash
uv run --dev pytest
```

## Linting, formatting and type checking

Ruff and pyrefly aren't project dependencies; they run through [pre-commit](https://pre-commit.com) hooks via
[`prek`](https://github.com/j178/prek), a drop-in pre-commit replacement, so the same versions are used locally and in
CI:

```bash
uvx prek run --all-files
```

Install it as a git hook so it runs automatically on every commit:

```bash
uvx prek install
```

## Testing

`uv run --dev pytest --cov` runs the suite in `tests/`. It never downloads a real spaCy model or hits the network:
`spacy.blank()` builds test `Doc`/`Token` objects directly, and [respx](https://lundberg.github.io/respx/) mocks every
Wiktionary HTTP response. `.github/workflows/test.yml`'s `smoke-test` job is the one place that actually downloads a
model and runs the script end to end, on every push.

## Evaluating changes to the skill

`pytest` covers `scripts/analyze_grammar.py`, not the quality of the grammar explanations an agent writes from its
output. That's what `evals/evals.json` is for: a set of realistic prompts, each with a human-readable
`expected_output` and a list of verifiable `expectations`, following the
[Claude Code skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) format.

If you change `SKILL.md` or `scripts/analyze_grammar.py` in a way that could affect the explanations an agent
produces, run the evals with `/skill-creator` (in Claude Code, on this repo). It spawns a subagent per eval case with
the skill available and a matched baseline subagent without it, grades each run's outputs against the case's
`expectations`, and aggregates the results into pass rates and a with/without delta. It writes everything to
`../analyze-grammar-skill-workspace/` (a sibling of this repo, not inside it, so nothing lands in git by accident).

When adding new eval cases to `evals/evals.json`, keep `expectations` objectively checkable from the transcript and
outputs (not "the explanation is good", but "the explanation states the mood is subjunctive"), and add at least one
negative control (a prompt that should *not* trigger the skill) alongside cases that should.

`.github/workflows/eval-skill.yml` runs a subset of `evals/evals.json` automatically (`evals/run_agy_evals.py`) using
the [Antigravity CLI](https://antigravity.google) (`agy`) against the free tier of the Gemini API, so regressions in
`SKILL.md` get caught without needing Claude Code. It injects `SKILL.md`'s content directly into the prompt rather
than relying on `agy`'s own skill discovery, since `agy` has no bare-`SKILL.md` auto-discovery from a project
directory the way Claude Code does; this also makes the run deterministic instead of depending on a specific CLI
version's trigger heuristics. Unlike the Claude Code harness above, it doesn't run a without-skill baseline: it's a
regression gate against each case's `expectations`, not a with/without comparison, so it grades every case it runs
and computes the pass rate itself from the grader's per-expectation verdicts rather than trusting a model-reported
summary. It runs on `workflow_dispatch` and on pushes to `main` that touch `SKILL.md`, `scripts/`, or the evals
themselves, gated on a `GEMINI_API_KEY` repository secret. `DEFAULT_MODEL` in `run_agy_evals.py` is pinned to the
model label that resolves under the pure `GEMINI_API_KEY` auth mode CI uses (`{"modelProvider": "gemini"}`, no GCP
project) — a different auth mode accepts different label formats for the same models, so if `agy`'s model naming
changes, a `workflow_dispatch` run with `--cases 1` is the fast way to find the new correct label.

## Before opening a pull request

- `uv run --dev pytest` and `uvx prek run --all-files` both pass.
- Keep commits atomic: one logical change per commit, with an imperative-mood message ("Add x", not "Added x" or "Adds
  x").
- New behavior gets a test; a bug fix gets a regression test.
- If you change `scripts/analyze_grammar.py`'s CLI or SKILL.md's instructions, check the other one is still consistent.

## AI usage policy

Using AI tools to accelerate your workflow, whether for prototyping, writing tests, or improving documentation, is
**encouraged**.

However, as a contributor, you remain **fully responsible** for the code and content you submit. Please ensure the
following:

1. **No "AI slop"**: don't submit unreviewed, low-quality, or redundant AI-generated content.
1. **Verify and test**: all AI-generated code must be reviewed, tested, and verified to work as intended.
1. **Maintainability**: the content must be clear, idiomatic, and maintainable by a human.
