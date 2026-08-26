# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "spacy",
#     "requests",
#     # spacy.cli.download() shells out to `python -m pip install`; uv's
#     # ephemeral venvs don't include pip unless it's listed as a dependency.
#     "pip",
# ]
# ///

"""Parse a sentence's grammar with spaCy, with a Wiktionary fallback for words spaCy can't classify."""

import argparse
import re
import sys
from urllib.parse import quote

import requests
import spacy
from spacy.cli import download
from spacy.util import is_package

# Fail fast: a slow dictionary lookup shouldn't stall the whole analysis,
# and there is no result worth waiting long for.
WIKTIONARY_TIMEOUT_SECONDS = 3

HTML_TAG_RE = re.compile(r"<[^>]+>")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Parse a sentence's grammar with spaCy, with a Wiktionary "
            "fallback for words spaCy can't classify."
        ),
    )
    parser.add_argument(
        "model_name",
        help=(
            "spaCy trained pipeline name, e.g. fr_core_news_md "
            "(see https://spacy.io/models). Falls back to the '_sm' size "
            "if an '_md' pipeline isn't available for the language."
        ),
    )
    parser.add_argument("sentence", help="The sentence to analyze.")
    return parser


def fetch_wiktionary_definition(word: str, lang_code: str | None) -> str | None:
    """Best-effort fallback definition from the Wiktionary REST API."""
    if lang_code is None:
        return None

    url = f"https://en.wiktionary.org/api/rest_v1/page/definition/{quote(word.lower(), safe='')}"
    headers = {"User-Agent": "analyze-grammar-skill (https://github.com/)"}

    try:
        response = requests.get(
            url, headers=headers, timeout=WIKTIONARY_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()
        entries = data.get(lang_code)
        if not entries:
            return None
        definitions = entries[0].get("definitions") or []
        if not definitions:
            return None
        clean_def = HTML_TAG_RE.sub("", definitions[0].get("definition", ""))
        return f"{entries[0].get('partOfSpeech', 'unknown')}: {clean_def}"
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def load_model(model_name: str) -> spacy.language.Language:
    """Load a spaCy pipeline, downloading it first if needed."""
    if not is_package(model_name):
        print(f"Downloading spaCy model '{model_name}'...", file=sys.stderr)
        try:
            download(model_name)
        except SystemExit:
            # Not every language ships an "_md" pipeline; retry with "_sm"
            # rather than making the caller guess which languages do.
            if model_name.endswith("_md"):
                fallback_name = f"{model_name.removesuffix('_md')}_sm"
                print(
                    f"'{model_name}' isn't available; falling back to '{fallback_name}'.",
                    file=sys.stderr,
                )
                return load_model(fallback_name)
            print(
                f"Error: failed to download model '{model_name}'. "
                "Confirm the exact package name at https://spacy.io/models.",
                file=sys.stderr,
            )
            sys.exit(1)
    return spacy.load(model_name)


def main() -> None:
    """Parse CLI args, run the spaCy pipeline, and print the analysis."""
    args = build_arg_parser().parse_args()

    nlp = load_model(args.model_name)
    # nlp.lang is the definitive language code (also the key Wiktionary
    # groups its definitions under); re-derive the name actually loaded
    # in case load_model() fell back to a different pipeline size.
    lang_code = nlp.lang
    resolved_name = f"{nlp.lang}_{nlp.meta['name']}"
    doc = nlp(args.sentence)

    print(f"--- Syntax analysis ({resolved_name}) for: {args.sentence} ---\n")
    for token in doc:
        morphology = str(token.morph) or "uninflected"
        print(f"Word: {token.text}")
        print(f"  Lemma: {token.lemma_}")
        print(f"  Part of speech: {token.pos_}")
        print(f"  Morphology: {morphology}")
        print(f"  Dependency: {token.dep_} (head -> {token.head.text})")

        # pos_ == "X" is spaCy's genuine "I don't know what this is" signal.
        # token.is_oov is unreliable here: "_sm" pipelines ship no word
        # vectors at all (every token reads as OOV), and even on "_md"/"_lg"
        # it reflects vector coverage, not tagging confidence.
        if token.pos_ == "X" and not token.is_punct and not token.is_space:
            fallback = fetch_wiktionary_definition(
                token.lemma_ or token.text, lang_code
            )
            if fallback:
                print(f"  Fallback dictionary lookup: {fallback}")

        print("-" * 30)


if __name__ == "__main__":
    main()
