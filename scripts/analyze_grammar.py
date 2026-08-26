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

"""Parse a sentence's grammar with spaCy, with a Wiktionary fallback for
words spaCy can't classify."""

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


def fetch_wiktionary_definition(word, lang_code):
    """Best-effort fallback definition from the Wiktionary REST API."""
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


def load_model(model_name):
    if not is_package(model_name):
        print(f"Downloading spaCy model '{model_name}'...", file=sys.stderr)
        try:
            download(model_name)
        except SystemExit:
            print(
                f"Error: failed to download model '{model_name}'. "
                "Confirm the exact package name at https://spacy.io/models.",
                file=sys.stderr,
            )
            sys.exit(1)
    return spacy.load(model_name)


def main():
    if len(sys.argv) != 3:
        print(
            'Usage: uv run scripts/analyze_grammar.py <spacy_model_name> "<sentence>"',
            file=sys.stderr,
        )
        print(
            'Example: uv run scripts/analyze_grammar.py fr_core_news_sm "Bonjour."',
            file=sys.stderr,
        )
        sys.exit(1)

    model_name, sentence = sys.argv[1], sys.argv[2]
    # spaCy pipeline names are "<lang>_<genre>_<size>"; the language code
    # prefix doubles as the key Wiktionary groups its definitions under.
    lang_code = model_name.split("_", 1)[0]

    nlp = load_model(model_name)
    doc = nlp(sentence)

    print(f"--- Syntax analysis ({model_name}) for: {sentence} ---\n")
    for token in doc:
        morphology = str(token.morph) if str(token.morph) else "uninflected"
        print(f"Word: {token.text}")
        print(f"  Lemma: {token.lemma_}")
        print(f"  Part of speech: {token.pos_}")
        print(f"  Morphology: {morphology}")
        print(f"  Dependency: {token.dep_} (head -> {token.head.text})")

        # pos_ == "X" is spaCy's genuine "I don't know what this is" signal.
        # token.is_oov is unreliable here: the small pipelines ship no word
        # vectors, so every token registers as out-of-vocabulary regardless
        # of whether spaCy actually recognized it.
        if token.pos_ == "X" and not token.is_punct and not token.is_space:
            fallback = fetch_wiktionary_definition(
                token.lemma_ or token.text, lang_code
            )
            if fallback:
                print(f"  Fallback dictionary lookup: {fallback}")

        print("-" * 30)


if __name__ == "__main__":
    main()
