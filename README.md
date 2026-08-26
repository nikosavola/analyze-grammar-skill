# analyze-grammar-skill

[![Test](https://github.com/nikosavola/analyze-grammar-skill/actions/workflows/test.yml/badge.svg)](https://github.com/nikosavola/analyze-grammar-skill/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/nikosavola/analyze-grammar-skill/graph/badge.svg)](https://codecov.io/gh/nikosavola/analyze-grammar-skill)
[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=nikosavola_analyze-grammar-skill&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=nikosavola_analyze-grammar-skill)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

An [Agent Skill](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) that grounds grammar
explanations in [spaCy](https://spacy.io/)'s dependency parser instead of an LLM's guesses, with a Wiktionary fallback
for words spaCy can't classify. Works with any language spaCy ships a trained pipeline for.

## Install

```bash
npx skills add nikosavola/analyze-grammar-skill
```

<details>
<summary>Install manually instead</summary>

Clone into your agent's skills directory. The folder name must match the skill's `name` field (`analyze-grammar`):

```bash
git clone https://github.com/nikosavola/analyze-grammar-skill.git ~/.claude/skills/analyze-grammar
```

</details>

## Requirements

- [`uv`](https://docs.astral.sh/uv/) — the script runs via `uv run`, which manages its own dependencies (spaCy, httpx)
  and downloads language models on demand per [PEP 723](https://peps.python.org/pep-0723/).

## How it works

Ask an agent to explain the grammar of a sentence in any language. It picks the matching spaCy
[trained pipeline name](https://spacy.io/models), preferring the `md` size (e.g. `fr_core_news_md`), and runs
`scripts/analyze_grammar.py <spacy_model_name> "<sentence>"`, which parses the sentence with spaCy (downloading the
model on first use, falling back to `sm` if a language has no `md` pipeline) and looks up any word spaCy can't classify
on Wiktionary. The agent uses that output as ground truth for its explanation.

See [SKILL.md](SKILL.md) for the full instructions given to the agent.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#eef2ff', 'primaryBorderColor': '#6366f1', 'primaryTextColor': '#312e81', 'actorBkg': '#eef2ff', 'actorBorder': '#6366f1', 'actorTextColor': '#312e81', 'signalColor': '#475569', 'signalTextColor': '#1e293b', 'noteBkgColor': '#fffbeb', 'noteBorderColor': '#f59e0b', 'noteTextColor': '#78350f'}}}%%
sequenceDiagram
    autonumber
    participant Agent
    participant Script as analyze_grammar.py
    participant spaCy
    participant Wiktionary

    Agent->>Script: uv run ... MODEL -- "SENTENCE"

    rect rgba(99, 102, 241, 0.08)
    Note over Script,spaCy: Load or download the model
    Script->>spaCy: load_model(MODEL)
    alt already installed
        spaCy-->>Script: pipeline
    else needs download
        Script->>spaCy: download(MODEL)
        alt _md unavailable
            Script->>spaCy: download(_sm fallback)
        end
        spaCy-->>Script: pipeline
    end
    end

    rect rgba(20, 184, 166, 0.08)
    Note over Script,spaCy: Parse the sentence
    Script->>spaCy: nlp(SENTENCE)
    spaCy-->>Script: Doc (tokens, pos, dep, morph)
    end

    rect rgba(244, 63, 94, 0.08)
    Note over Script,Wiktionary: Concurrent fallback lookups via asyncio.gather
    par
        Script->>Wiktionary: GET definition (token, pos is X)
        Script->>Wiktionary: GET definition (token, pos is X)
    end
    Wiktionary-->>Script: definitions or 404
    end

    rect rgba(245, 158, 11, 0.08)
    Script-->>Agent: syntax analysis plus fallback definitions
    Note over Agent: writes the conversational explanation (SKILL.md Step 2)
    end
```
