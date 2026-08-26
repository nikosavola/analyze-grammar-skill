"""Tests for scripts/analyze_grammar.py."""

from typing import ClassVar

import analyze_grammar as ag
import httpx
import pytest
import respx
import spacy

WIKTIONARY_URL = "https://en.wiktionary.org/api/rest_v1/page/definition/chien"


def make_doc(
    words: list[str], pos_overrides: dict[int, str] | None = None
) -> spacy.tokens.Doc:
    """Build a blank-pipeline Doc, optionally overriding pos_ for some tokens by index."""
    doc = spacy.blank("en")(" ".join(words))
    for i, pos in (pos_overrides or {}).items():
        doc[i].pos_ = pos
    return doc


def test_build_arg_parser_parses_model_name_and_sentence() -> None:
    args = ag.build_arg_parser().parse_args(["fr_core_news_md", "Bonjour le monde."])
    assert args.model_name == "fr_core_news_md"
    assert args.sentence == "Bonjour le monde."


def test_build_arg_parser_requires_both_arguments() -> None:
    with pytest.raises(SystemExit):
        ag.build_arg_parser().parse_args(["fr_core_news_md"])


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


async def test_fetch_fallback_definitions_skips_classified_tokens() -> None:
    doc = make_doc(["Hello", "world"])
    assert await ag.fetch_fallback_definitions(doc, "en") == {}


async def test_fetch_fallback_definitions_skips_when_lang_code_is_none() -> None:
    doc = make_doc(["wuggle"], {0: "X"})
    assert await ag.fetch_fallback_definitions(doc, None) == {}


@respx.mock
async def test_fetch_fallback_definitions_dispatches_lookups_concurrently() -> None:
    doc = make_doc(["wuggle", "and", "blergon"], {0: "X", 2: "X"})

    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/wuggle").mock(
        return_value=httpx.Response(
            200,
            json={
                "en": [
                    {
                        "partOfSpeech": "Noun",
                        "definitions": [{"definition": "a wuggle"}],
                    }
                ]
            },
        )
    )
    respx.get("https://en.wiktionary.org/api/rest_v1/page/definition/blergon").mock(
        return_value=httpx.Response(404)
    )

    result = await ag.fetch_fallback_definitions(doc, "en")

    # both lookups are dispatched via asyncio.gather, not one-at-a-time
    assert respx.calls.call_count == 2
    # a failed lookup is dropped rather than stored as None
    assert result == {0: "Noun: a wuggle"}


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
    assert "falling back to 'fr_core_news_sm'" in capsys.readouterr().err


def test_load_model_exits_when_download_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_download(_name: str) -> None:
        raise SystemExit(1)

    monkeypatch.setattr(ag, "is_package", lambda _name: False)
    monkeypatch.setattr(ag, "download", fake_download)

    with pytest.raises(SystemExit):
        ag.load_model("totally_bogus_sm")


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
        return_value=httpx.Response(
            200,
            json={
                "en": [
                    {
                        "partOfSpeech": "Noun",
                        "definitions": [{"definition": "a made-up creature"}],
                    }
                ]
            },
        )
    )

    await ag.main(["en_core_web_sm", "wuggle jumped"])

    out = capsys.readouterr().out
    assert "Fallback dictionary lookup: Noun: a made-up creature" in out
