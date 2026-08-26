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
