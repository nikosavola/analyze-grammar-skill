"""Tests for scripts/analyze_grammar.py."""

import asyncio
from typing import TYPE_CHECKING, ClassVar
from unittest.mock import patch
from urllib.parse import unquote

import analyze_grammar as ag
import httpx
import pytest
import respx
import spacy
from hypothesis import given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

WIKTIONARY_URL = "https://en.wiktionary.org/api/rest_v1/page/definition/chien"


def make_doc(
    words: list[str], pos_overrides: dict[int, str] | None = None
) -> spacy.tokens.Doc:
    """Build a blank-pipeline Doc, optionally overriding pos_ for some tokens by index."""
    doc = spacy.blank("en")(" ".join(words))
    for i, pos in (pos_overrides or {}).items():
        doc[i].pos_ = pos
    return doc


def definition_response(pos: str, definition: str, lang: str = "en") -> httpx.Response:
    """Build a minimal well-formed Wiktionary definition response body."""
    return httpx.Response(
        200,
        json={
            lang: [{"partOfSpeech": pos, "definitions": [{"definition": definition}]}]
        },
    )


def test_build_arg_parser_parses_model_name_and_sentence() -> None:
    args = ag.build_arg_parser().parse_args(["fr_core_news_md", "Bonjour le monde."])
    assert args.model_name == "fr_core_news_md"
    assert args.sentence == "Bonjour le monde."


def test_build_arg_parser_requires_both_arguments() -> None:
    with pytest.raises(SystemExit):
        ag.build_arg_parser().parse_args(["fr_core_news_md"])


@given(st.text(min_size=1, max_size=40))
def test_wiktionary_url_round_trips_any_title(title: str) -> None:
    url = ag.wiktionary_url(title)
    assert url.startswith("https://en.wiktionary.org/api/rest_v1/page/definition/")
    encoded_title = url.removeprefix(
        "https://en.wiktionary.org/api/rest_v1/page/definition/"
    )
    assert unquote(encoded_title) == title


@respx.mock
async def test_fetch_wiktionary_definition_returns_first_sense() -> None:
    respx.get(WIKTIONARY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "fr": [
                    {
                        "partOfSpeech": "Noun",
                        "definitions": [{"definition": "<a href='/wiki/dog'>dog</a>"}],
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result == "Noun: dog"


@respx.mock
async def test_fetch_wiktionary_definition_returns_none_for_missing_language() -> None:
    respx.get(WIKTIONARY_URL).mock(return_value=httpx.Response(200, json={"en": []}))
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result is None


@respx.mock
async def test_fetch_wiktionary_definition_returns_none_for_empty_definitions() -> None:
    respx.get(WIKTIONARY_URL).mock(
        return_value=httpx.Response(
            200, json={"fr": [{"partOfSpeech": "Noun", "definitions": []}]}
        )
    )
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result is None


@respx.mock
async def test_fetch_wiktionary_definition_returns_none_on_http_error() -> None:
    respx.get(WIKTIONARY_URL).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result is None


@respx.mock
async def test_fetch_wiktionary_definition_returns_none_on_malformed_json() -> None:
    respx.get(WIKTIONARY_URL).mock(return_value=httpx.Response(200, text="not json"))
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result is None


@respx.mock
async def test_fetch_wiktionary_definition_returns_none_when_body_is_a_list() -> None:
    """A 200 with valid-but-wrong-shaped JSON (list, not dict) must not crash."""
    respx.get(WIKTIONARY_URL).mock(return_value=httpx.Response(200, json=[]))
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result is None


@respx.mock
async def test_fetch_wiktionary_definition_returns_none_when_entry_is_a_string() -> (
    None
):
    respx.get(WIKTIONARY_URL).mock(
        return_value=httpx.Response(200, json={"fr": ["oops"]})
    )
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result is None


@respx.mock
async def test_fetch_wiktionary_definition_returns_none_when_entries_not_a_list() -> (
    None
):
    respx.get(WIKTIONARY_URL).mock(return_value=httpx.Response(200, json={"fr": 42}))
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "chien", "fr")
    assert result is None


@respx.mock
async def test_fetch_wiktionary_definition_prefers_exact_case() -> None:
    """German nouns are canonically capitalized; a lowercased-only lookup would miss them."""
    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/Berlin").mock(
        return_value=definition_response("Proper noun", "capital of Germany", lang="de")
    )
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "Berlin", "de")
    assert result == "Proper noun: capital of Germany"
    assert respx.calls.call_count == 1  # exact case succeeded; no lowercase retry


@respx.mock
async def test_fetch_wiktionary_definition_falls_back_to_lowercase() -> None:
    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/Bonjour").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/bonjour").mock(
        return_value=definition_response("Interjection", "hello", lang="fr")
    )
    async with httpx.AsyncClient() as client:
        result = await ag.fetch_wiktionary_definition(client, "Bonjour", "fr")
    assert result == "Interjection: hello"


@given(
    st.lists(
        st.sampled_from(["X", "NOUN", "VERB", "ADJ", "ADV", "PRON"]),
        min_size=1,
        max_size=8,
    )
)
def test_tokens_needing_lookup_matches_exactly_the_x_tagged_tokens(
    pos_tags: list[str],
) -> None:
    words = [f"word{i}" for i in range(len(pos_tags))]
    doc = make_doc(words, dict(enumerate(pos_tags)))
    result_indices = {token.i for token in ag.tokens_needing_lookup(doc)}
    expected_indices = {i for i, pos in enumerate(pos_tags) if pos == "X"}
    assert result_indices == expected_indices


async def test_fetch_fallback_definitions_skips_classified_tokens() -> None:
    doc = make_doc(["Hello", "world"])
    assert await ag.fetch_fallback_definitions(doc, "en") == {}


async def test_fetch_fallback_definitions_skips_when_lang_code_is_none() -> None:
    doc = make_doc(["wuggle"], {0: "X"})
    assert await ag.fetch_fallback_definitions(doc, None) == {}


@respx.mock
async def test_fetch_fallback_definitions_maps_results_and_drops_failures() -> None:
    doc = make_doc(["wuggle", "and", "blergon"], {0: "X", 2: "X"})

    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/wuggle").mock(
        return_value=definition_response("Noun", "a wuggle")
    )
    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/blergon").mock(
        return_value=httpx.Response(404)
    )

    result = await ag.fetch_fallback_definitions(doc, "en")

    # the failed lookup ("blergon") is dropped rather than stored as None
    assert result == {0: "Noun: a wuggle"}


@respx.mock
async def test_fetch_fallback_definitions_dispatches_lookups_concurrently() -> None:
    """Both lookups must genuinely be in flight at once, not run one after another.

    Each mock handler blocks until *both* requests have started. A
    non-concurrent implementation (e.g. a plain for-loop of awaits instead
    of asyncio.gather) would deadlock here: the second request would never
    start while the handler for the first is still waiting on it.
    """
    doc = make_doc(["wuggle", "blergon"], {0: "X", 1: "X"})
    started: set[str] = set()
    both_started = asyncio.Event()

    def handler_for(
        word: str, pos: str, definition: str
    ) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
        async def handler(_request: httpx.Request) -> httpx.Response:
            started.add(word)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return definition_response(pos, definition)

        return handler

    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/wuggle").mock(
        side_effect=handler_for("wuggle", "Noun", "a wuggle")
    )
    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/blergon").mock(
        side_effect=handler_for("blergon", "Noun", "a blergon")
    )

    result = await ag.fetch_fallback_definitions(doc, "en")

    assert result == {0: "Noun: a wuggle", 1: "Noun: a blergon"}


def test_load_model_returns_directly_when_already_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ag, "is_package", lambda _name: True)
    download_calls = []
    monkeypatch.setattr(ag, "download", download_calls.append)
    sentinel = object()
    monkeypatch.setattr(ag.spacy, "load", lambda _name: sentinel)

    assert ag.load_model("fr_core_news_sm") is sentinel
    assert download_calls == []


def test_load_model_downloads_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ag, "is_package", lambda _name: False)
    download_calls = []
    monkeypatch.setattr(ag, "download", download_calls.append)
    sentinel = object()
    monkeypatch.setattr(ag.spacy, "load", lambda _name: sentinel)

    assert ag.load_model("fr_core_news_sm") is sentinel
    assert download_calls == ["fr_core_news_sm"]


def test_load_model_falls_back_from_md_to_sm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ag, "is_package", lambda _name: False)

    def fake_download(name: str) -> None:
        if name.endswith("_md"):
            raise SystemExit(1)

    monkeypatch.setattr(ag, "download", fake_download)
    sentinel = object()
    monkeypatch.setattr(ag.spacy, "load", lambda _name: sentinel)

    assert ag.load_model("fr_core_news_md") is sentinel
    assert "retrying with 'fr_core_news_sm'" in capsys.readouterr().err


def test_load_model_exits_when_download_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_download(_name: str) -> None:
        raise SystemExit(1)

    monkeypatch.setattr(ag, "is_package", lambda _name: False)
    monkeypatch.setattr(ag, "download", fake_download)

    with pytest.raises(SystemExit) as exc_info:
        ag.load_model("totally_bogus_sm")
    assert exc_info.value.code == 1


@given(
    st.text(
        alphabet=st.characters(whitelist_categories=["Ll"]), min_size=1, max_size=15
    )
)
@settings(deadline=None)
def test_load_model_always_retries_md_with_sm_variant(prefix: str) -> None:
    model_name = f"{prefix}_md"
    attempted = []

    def fake_download(name: str) -> None:
        attempted.append(name)
        raise SystemExit(1)

    # unittest.mock.patch, not the monkeypatch fixture: function-scoped
    # fixtures are only set up once per test node, not once per Hypothesis
    # example, which is exactly the mismatch Hypothesis's
    # function_scoped_fixture health check exists to catch.
    with (
        patch.object(ag, "is_package", lambda _name: False),
        patch.object(ag, "download", fake_download),
        pytest.raises(SystemExit),
    ):
        ag.load_model(model_name)

    assert attempted == [model_name, f"{prefix}_sm"]


async def test_main_wires_parsing_lookup_and_output_together(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A blank pipeline avoids a real model download while still exercising
    # the full main() flow: real tokenization, real Doc/Token attributes.
    # It never tags anything "X", so no Wiktionary lookup is triggered here
    # (that path is covered separately by the fetch_fallback_definitions tests).
    monkeypatch.setattr(ag, "load_model", lambda _name: spacy.blank("en"))

    await ag.main(["en_core_web_sm", "Hello world"])

    out = capsys.readouterr().out
    assert "--- Syntax analysis (en_pipeline) for: Hello world ---" in out
    assert "Word [0]: Hello" in out
    assert "Word [1]: world" in out


class NlpWithMorphOnFirstToken:
    """Blank-pipeline stand-in that sets morphology on its first token only, so main()
    exercises both the populated-Morphology branch and the "uninflected" fallback."""

    lang = "en"
    meta: ClassVar = {"name": "pipeline"}

    def __call__(self, sentence: str) -> spacy.tokens.Doc:
        doc = spacy.blank("en")(sentence)
        doc[0].set_morph("Number=Sing|Person=2")  # pyrefly: ignore  # real method, stub is incomplete
        return doc


async def test_main_prints_morphology_one_feature_per_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A packed "Number=Sing|Person=2" string is what token.morph used to print on one
    # line; main() must now expand it under an indented "Morphology:" header instead,
    # and fall back to "uninflected" for a token with no morphology at all.
    monkeypatch.setattr(ag, "load_model", lambda _name: NlpWithMorphOnFirstToken())

    await ag.main(["en_core_web_sm", "tu fasses"])

    out = capsys.readouterr().out
    assert "  Morphology:\n    Number: Sing\n    Person: 2\n" in out
    assert "  Morphology: uninflected\n" in out


class NlpWithUnknownToken:
    """Blank-pipeline stand-in that tags its first token "X" (unclassified),
    so main() actually exercises the Wiktionary fallback path end to end."""

    lang = "en"
    meta: ClassVar = {"name": "pipeline"}

    def __call__(self, sentence: str) -> spacy.tokens.Doc:
        doc = spacy.blank("en")(sentence)
        doc[0].pos_ = "X"
        return doc


@respx.mock
async def test_main_prints_wiktionary_fallback_for_unclassified_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ag, "load_model", lambda _name: NlpWithUnknownToken())
    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/wuggle").mock(
        return_value=definition_response("Noun", "a made-up creature")
    )

    await ag.main(["en_core_web_sm", "wuggle jumped"])

    out = capsys.readouterr().out
    assert "Fallback dictionary lookup: Noun: a made-up creature" in out
