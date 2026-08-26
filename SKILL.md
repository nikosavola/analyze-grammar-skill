---
name: analyze-grammar
description: Parses the grammar, syntax, and morphology of a sentence in any language using spaCy's dependency parser, with a Wiktionary fallback for words spaCy tags as unrecognized. Use when the user asks to explain, break down, or analyze the grammar, syntax, verb conjugation, word roles, or sentence structure of a sentence. Do not use for plain translation requests, spelling/style checks, or generating practice sentences.
---

# Grammar Analyzer

Explaining sentence grammar from model knowledge alone risks hallucinated tags and conjugations. Ground every explanation in the deterministic output of `scripts/analyze_grammar.py`, a spaCy dependency parser that runs via `uv run` with no setup.

## Step 1: Run the analysis script

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run scripts/analyze_grammar.py <language_code> "<sentence>"
```

- `language_code` is the 2-letter ISO code (`fr`, `es`, `de`, ...). Run the script with no arguments to print supported codes.
- The spaCy model for that language downloads on first use and is cached by `uv` for later runs; the first call for a given language can take up to a minute.
- Example: `uv run scripts/analyze_grammar.py es "Me gusta mucho leer."`

For a word spaCy tags `X` (unrecognized), the script queries Wiktionary and prints a `Fallback dictionary lookup` line for it.

## Step 2: Explain the sentence

Using the script's output as ground truth, write a conversational Markdown explanation covering:

- The root verb, main clause, and any dependent clauses.
- Tense, mood, and agreement, from the `Morphology` line.
- Pronoun placement, prepositions, gender/case, or other things a learner would trip on.
- Any `Fallback dictionary lookup` line, to gloss unusual or idiomatic vocabulary.

Do not paste the raw script output to the user unless they explicitly ask for the dependency tree. Tailor depth to the user's stated level if known (e.g. skip basic tense explanations for an advanced learner).
