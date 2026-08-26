---
name: analyze-grammar
description: Parses the grammar, syntax, and morphology of a sentence in any language using spaCy's dependency parser, with a Wiktionary fallback for words spaCy tags as unrecognized. Use when the user asks to explain, break down, or analyze the grammar, syntax, verb conjugation, word roles, or sentence structure of a sentence. Do not use for plain translation requests, spelling/style checks, or generating practice sentences.
when_to_use: |
  - User asks "what tense/mood/case is this", "why is this word here", or similar about a specific sentence.
  - User is learning a language and asks for a grammatical breakdown of an example sentence.
  - User asks for a dependency or syntax tree.
  - Do NOT use for translation alone, vocabulary lookup with no grammar question, or generating new example sentences.
argument-hint: <spacy_model_name> "<sentence>" (e.g. fr_core_news_md "Il faut que tu le fasses.")
---

# Grammar Analyzer

Explaining sentence grammar from model knowledge alone risks hallucinated tags and conjugations. Ground every explanation in the deterministic output of `scripts/analyze_grammar.py`, a spaCy dependency parser that runs via `uv run` with no setup.

## Step 1: Run the analysis script

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run scripts/analyze_grammar.py <spacy_model_name> "<sentence>"
```

- `spacy_model_name` is a spaCy trained pipeline name, not a language code. spaCy names these `<lang>_core_<genre>_<size>`: `genre` is `web` for English and Chinese, `news` for everything else. Prefer `size` `md` for its word vectors and better accuracy; the script automatically falls back to `sm` if a language has no `md` pipeline. Example: French is `fr_core_news_md`, English is `en_core_web_md`.
- If unsure of the exact name for a language, check https://spacy.io/models for the full, current list before running the script.
- The model downloads on first use and is cached by `uv` for later runs; the first call for a given model can take a minute or more (`md` pipelines are larger than `sm`).
- Example: `uv run scripts/analyze_grammar.py es_core_news_md "Me gusta mucho leer."`
- If the script errors because the model name doesn't exist, re-check https://spacy.io/models and retry with the correct name.

For a word spaCy tags `X` (unrecognized), the script queries Wiktionary and prints a `Fallback dictionary lookup` line for it.

## Step 2: Explain the sentence

Using the script's output as ground truth, write a conversational Markdown explanation covering:

- The root verb, main clause, and any dependent clauses.
- Tense, mood, and agreement, from the `Morphology` line.
- Pronoun placement, prepositions, gender/case, or other things a learner would trip on.
- Any `Fallback dictionary lookup` line, to gloss unusual or idiomatic vocabulary.

Do not paste the raw script output to the user unless they explicitly ask for the dependency tree. Tailor depth to the user's stated level if known (e.g. skip basic tense explanations for an advanced learner).
