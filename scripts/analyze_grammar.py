# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "spacy",
#     "httpx",
#     # spacy.cli.download() shells out to `python -m pip install`; uv's
#     # ephemeral venvs don't include pip unless it's listed as a dependency.
#     "pip",
# ]
# ///

"""Parse a sentence's grammar with spaCy, with a Wiktionary fallback for words spaCy can't classify."""

import argparse
import asyncio
import re
import sys
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
import spacy
from spacy.cli import download
from spacy.util import is_package

if TYPE_CHECKING:
    from spacy.tokens import Doc, Token

# Fail fast: a slow dictionary lookup shouldn't stall the whole analysis.
# This is httpx's per-request timeout, but since every lookup for a sentence
# runs concurrently via asyncio.gather, an ordinary failure (DNS, connection
# refused, a 5xx, no response) still bounds the whole batch to roughly this
# long, not this long multiplied by the number of unrecognized words.
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


def wiktionary_url(title: str) -> str:
    """Build the Wiktionary REST API definition URL for a page title."""
    return (
        f"https://en.wiktionary.org/api/rest_v1/page/definition/{quote(title, safe='')}"
    )


async def _fetch_definition_for_title(
    client: httpx.AsyncClient, title: str, lang_code: str
) -> str | None:
    try:
        response = await client.get(wiktionary_url(title))
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
    # AttributeError/TypeError guard against a 200 response whose JSON is
    # validly-formed but not shaped like Wiktionary's usual definition
    # payload (e.g. a list instead of a dict, or a string in place of an
    # entry dict) - "malformed" isn't limited to non-JSON bodies.
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
    ):
        return None


async def fetch_wiktionary_definition(
    client: httpx.AsyncClient, word: str, lang_code: str
) -> str | None:
    """Best-effort fallback definition from the Wiktionary REST API.

    Tries the word's exact casing first, since Wiktionary page titles are
    case-sensitive (e.g. German nouns are canonically capitalized), then
    retries lowercased if that title doesn't exist.
    """
    definition = await _fetch_definition_for_title(client, word, lang_code)
    if definition is not None:
        return definition
    lowered = word.lower()
    if lowered == word:
        return None
    return await _fetch_definition_for_title(client, lowered, lang_code)


def tokens_needing_lookup(doc: Doc) -> list[Token]:
    """Tokens spaCy couldn't classify, worth a Wiktionary lookup.

    pos_ == "X" is spaCy's genuine "I don't know what this is" signal.
    token.is_oov is unreliable here: "_sm" pipelines ship no word vectors
    at all (every token reads as OOV), and even on "_md"/"_lg" it reflects
    vector coverage, not tagging confidence.
    """
    return [
        token
        for token in doc
        if token.pos_ == "X" and not token.is_punct and not token.is_space
    ]


async def fetch_fallback_definitions(doc: Doc, lang_code: str | None) -> dict[int, str]:
    """Look up every spaCy-unclassified token in `doc` on Wiktionary, concurrently."""
    lookup_tokens = tokens_needing_lookup(doc)
    if not lookup_tokens or lang_code is None:
        return {}

    headers = {
        "User-Agent": "analyze-grammar-skill (https://github.com/nikosavola/analyze-grammar-skill)"
    }
    async with httpx.AsyncClient(
        headers=headers, timeout=WIKTIONARY_TIMEOUT_SECONDS
    ) as client:
        results = await asyncio.gather(
            *(
                fetch_wiktionary_definition(
                    client, token.lemma_ or token.text, lang_code
                )
                for token in lookup_tokens
            )
        )
    return {
        token.i: definition
        for token, definition in zip(lookup_tokens, results, strict=True)
        if definition is not None
    }


def load_model(model_name: str) -> spacy.language.Language:
    """Load a spaCy pipeline, downloading it first if needed."""
    if not is_package(model_name):
        print(f"Downloading spaCy model '{model_name}'...", file=sys.stderr)
        try:
            download(model_name)
        except SystemExit:
            # Not every language ships an "_md" pipeline, and spaCy raises
            # the same SystemExit whether the name doesn't exist or the
            # download itself failed (network blip, registry outage) - we
            # can't tell those apart, so this message doesn't claim either.
            if model_name.endswith("_md"):
                fallback_name = f"{model_name.removesuffix('_md')}_sm"
                print(
                    f"Couldn't download '{model_name}'; retrying with '{fallback_name}'.",
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


async def main(argv: list[str] | None = None) -> None:
    """Parse CLI args, run the spaCy pipeline, and print the analysis."""
    args = build_arg_parser().parse_args(argv)

    nlp = load_model(args.model_name)
    # nlp.lang is the definitive language code (also the key Wiktionary
    # groups its definitions under); re-derive the name actually loaded
    # in case load_model() fell back to a different pipeline size.
    lang_code = nlp.lang
    resolved_name = f"{nlp.lang}_{nlp.meta['name']}"
    doc = nlp(args.sentence)

    fallbacks = await fetch_fallback_definitions(doc, lang_code)

    print(f"--- Syntax analysis ({resolved_name}) for: {args.sentence} ---\n")
    for token in doc:
        morphology = str(token.morph) or "uninflected"
        print(f"Word [{token.i}]: {token.text}")
        print(f"  Lemma: {token.lemma_}")
        print(f"  Part of speech: {token.pos_}")
        print(f"  Morphology: {morphology}")
        # head.text alone is ambiguous when a word repeats in the sentence
        # (e.g. two "le"s); the index pins down exactly which token it is.
        print(
            f"  Dependency: {token.dep_} (head -> {token.head.text} [{token.head.i}])"
        )
        if token.i in fallbacks:
            print(f"  Fallback dictionary lookup: {fallbacks[token.i]}")
        print("-" * 30)


if __name__ == "__main__":
    asyncio.run(main())
