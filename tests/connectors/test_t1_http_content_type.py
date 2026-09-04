"""T-026: what the client believed a response body WAS, and what it actually was.

`arrival.http.client`'s own module docstring is the specification these tests restore the
code to:

    * **Degradation (DESIGN Decision 8).**  A 500, a timeout, a DNS failure, **a body that
      is not text** -- every one of them is `None`.

It was not true.  Two measured defects:

1. **A response with no `Content-Type` was assumed to be HTML**, so a JSON payload served
   without a label was run through the HTML extractor.  Measured on
   `{"bio": "<b>founder</b> & <i>investor</i>", "expr": "a<b and c>d"}`: the extractor
   returned `{"name": ..., "bio": "founder & investor", "expr": "ad"}` -- nine characters
   deleted from the middle of a string VALUE, and the document no longer parsed as the
   same JSON.  A connector citing that has cited something the source never said.

2. **A binary body became a mojibake `RawDoc` instead of `None`.**  Measured on a PNG
   served as `image/png`: `fetch_text` returned a `RawDoc` whose text opened
   `'�PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR...'` and carried 150 U+FFFD replacement
   characters.  The same held for `application/pdf` and `application/octet-stream`.  That
   document is quotable by T-3 and displayable by T-7.

WHY THE FIX SNIFFS THE BODY AND NOT ONLY THE LABEL.  A label is a claim by the origin, and
the two failure directions are not symmetric: an unlabelled JSON API is common and benign,
while a PNG served as `text/html` is exactly the case a label-only check misses.  So the
declared type gates the obviously-binary families and the decoded text is inspected for
what it actually contains.  The guard tests below are the other half of that: prose with
accents, with emoji, and in a legacy encoding must all still be text.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import extract as extract_module
from arrival.http import ratelimit as ratelimit_module
from arrival.http.client import fetch_json, fetch_record, fetch_text

pytestmark = pytest.mark.ticket("T-1")

_URL = "https://payload.example.com/thing"


def _serve(monkeypatch, status: int, headers: dict[str, str], content: bytes):
    seen: list[httpx.Request] = []

    async def handle(self, request, **_):
        seen.append(request)
        return httpx.Response(status, headers=headers, content=content, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()
    return seen


def _png(padding: int = 400) -> bytes:
    """A real PNG signature and IHDR chunk, then image data."""
    return (
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000100000001008060000001ff3ff61")
        + bytes(range(256)) * (padding // 256 + 1)
    )


# --- 1. an unlabelled body must not be assumed to be HTML ---------------------------


_TRICKY = {
    "name": "Teodoro Vance",
    "bio": "<b>founder</b> & <i>investor</i>",
    "expr": "a<b and c>d",
    "tags": ["looms", "scheduling"],
}


def test_a_json_body_with_no_content_type_is_not_run_through_the_html_extractor(
    monkeypatch, tmp_path
):
    _serve(monkeypatch, 200, {}, json.dumps(_TRICKY).encode())

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, "an unlabelled JSON payload is still a document"
    assert json.loads(doc.text) == _TRICKY, (
        f"the payload did not survive the round trip: {doc.text!r}. An absent "
        "Content-Type defaulted to text/html and the HTML extractor ate the angle "
        "brackets inside string VALUES."
    )
    assert "a<b and c>d" in doc.text, "nine characters were deleted from the middle of a value"


def test_an_unlabelled_html_page_is_still_extracted_as_html(monkeypatch, tmp_path):
    """The guard on the fix: unlabelled HTML pages exist and must not regress to raw
    markup in `RawDoc.text`, which is what T-3 quotes verbatim."""
    markup = (
        "<html><head><title>Thornfield Loom</title></head><body>"
        "<p>Thornfield Loom publishes a monthly maintenance almanac.</p>"
        "<script>var tracking = 1;</script></body></html>"
    )
    _serve(monkeypatch, 200, {}, markup.encode())

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None
    assert "Thornfield Loom publishes a monthly maintenance almanac." in doc.text
    assert "<p>" not in doc.text and "</html>" not in doc.text
    assert "var tracking" not in doc.text
    assert doc.title == "Thornfield Loom"


def test_an_unlabelled_plain_text_body_passes_through(monkeypatch, tmp_path):
    body = "Thornfield Loom, founded 2014 in Marfa, Texas.\nStill privately held."
    _serve(monkeypatch, 200, {}, body.encode())

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None
    assert "Marfa, Texas" in doc.text
    assert "Still privately held." in doc.text


def test_a_declared_content_type_is_believed_over_the_sniffer(monkeypatch, tmp_path):
    """Sniffing is a fallback for a MISSING label, never an override of a present one."""
    markup = "<html><body><p>Vance chairs the loom guild.</p></body></html>"
    _serve(monkeypatch, 200, {"content-type": "text/html; charset=utf-8"}, markup.encode())

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))
    assert doc is not None and "<p>" not in doc.text


# --- 2. a body that is not text is None, as the module docstring promises -----------


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("image/png", _png()),
        ("image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + bytes(range(256)) * 2),
        ("application/pdf", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + bytes(range(256)) * 2),
        ("application/zip", b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + bytes(range(256)) * 2),
        ("application/octet-stream", bytes(range(256)) * 3),
        ("font/woff2", b"wOF2\x00\x01\x00\x00" + bytes(range(256)) * 2),
        ("audio/mpeg", b"ID3\x04\x00\x00\x00" + bytes(range(256)) * 2),
        ("video/mp4", b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 2),
    ],
)
def test_a_binary_body_is_none_as_the_module_docstring_promises(
    monkeypatch, tmp_path, content_type, body
):
    """`client.py`'s docstring: "a body that is not text -- every one of them is `None`".

    It was returning a `RawDoc` whose `text` was the body decoded with `errors="replace"`,
    i.e. a citation to mojibake that T-3 can quote and T-7 can display.
    """
    _serve(monkeypatch, 200, {"content-type": content_type}, body)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is None, (
        f"a {content_type} body produced a RawDoc instead of None; its text began "
        f"{getattr(doc, 'text', '')[:40]!r}"
    )


def test_binary_bytes_mislabelled_as_html_are_still_none(monkeypatch, tmp_path):
    """A label is a claim by the origin, not a fact. This is the case a label-only
    check cannot see, and it is the common one: a CDN serving `text/html` for
    everything it does not recognise."""
    _serve(monkeypatch, 200, {"content-type": "text/html; charset=utf-8"}, _png())

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None


def test_binary_bytes_with_no_content_type_at_all_are_none(monkeypatch, tmp_path):
    _serve(monkeypatch, 200, {}, _png())

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None


def test_a_binary_body_is_none_from_fetch_record_and_fetch_json_too(monkeypatch, tmp_path):
    """The rejection belongs to the one door, not to `fetch_text`: `fetch_record`'s two
    direct callers (search, self_page) parse `record.body` themselves."""
    settings = settings_for(tmp_path)
    _serve(monkeypatch, 200, {"content-type": "application/pdf"}, b"%PDF-1.4\n" + _png())

    assert asyncio.run(fetch_record(_URL, settings=settings)) is None
    assert asyncio.run(fetch_json(_URL, settings=settings)) is None


def test_a_binary_body_does_not_come_back_as_text_on_the_second_call(monkeypatch, tmp_path):
    """Whatever the cache does with a rejected body, the answer may not change between
    the fresh path and the warm one."""
    settings = settings_for(tmp_path)
    _serve(monkeypatch, 200, {"content-type": "image/png"}, _png())

    assert asyncio.run(fetch_text(_URL, settings=settings)) is None
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None
    assert asyncio.run(fetch_record(_URL, settings=settings)) is None


# --- 3. guards: real text must not be mistaken for binary ---------------------------


def test_prose_with_accents_and_emoji_is_not_mistaken_for_binary(monkeypatch, tmp_path):
    body = (
        "<html><body><p>Zoë Fernández-Ruíz co-founded Thornfield Loom in Marfa. "
        "Her team ships every Friday 🎉 and the café downstairs supplies the coffee — "
        "€4 a cup, naturally.</p></body></html>"
    )
    _serve(monkeypatch, 200, {"content-type": "text/html; charset=utf-8"}, body.encode())

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, "UTF-8 prose is text"
    assert "Zoë Fernández-Ruíz" in doc.text and "🎉" in doc.text


def test_a_legacy_encoded_page_is_still_text_even_though_it_is_not_utf8(
    monkeypatch, tmp_path
):
    """A latin-1 page with no declared charset decodes with replacement characters. That
    is a lossy read of a TEXT document, not a binary body, and dropping it would lose a
    page the old code at least returned."""
    prose = (
        "<html><body><p>Le Metier Fernandez a ete fonde a Marfa. "
        + ("Une maison de tissage independante. " * 30)
        + "Cout: 4 euros.</p></body></html>"
    )
    body = prose.encode("latin-1").replace(b"ete", b"\xe9t\xe9").replace(b"Cout", b"Co\xfbt")
    _serve(monkeypatch, 200, {"content-type": "text/html"}, body)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, (
        "a latin-1 page is a text document read imperfectly; it must not be discarded "
        "as binary"
    )
    assert "Une maison de tissage independante." in doc.text


def test_a_json_api_answering_with_an_empty_list_is_still_a_document(monkeypatch, tmp_path):
    """`[]` is an answer -- "this source has nothing on her" -- and must not be confused
    with a body that is not text."""
    _serve(monkeypatch, 200, {"content-type": "application/json"}, b"[]")

    assert asyncio.run(fetch_json(_URL, settings=settings_for(tmp_path))) == []


def test_a_json_flavoured_media_type_is_not_treated_as_binary(monkeypatch, tmp_path):
    """`application/vnd.github+json` and friends are JSON. A binary check that matched
    `application/vnd.` would delete the GitHub connector's every response."""
    payload = {"login": "vance", "name": "Teodoro Vance"}
    for content_type in (
        "application/vnd.github+json",
        "application/vnd.api+json",
        "application/ld+json",
        "application/problem+json",
    ):
        _serve(monkeypatch, 200, {"content-type": content_type}, json.dumps(payload).encode())
        assert asyncio.run(fetch_json(_URL, settings=settings_for(tmp_path))) == payload, (
            f"{content_type} is JSON, not a binary payload"
        )


def test_an_xml_flavoured_media_type_is_not_treated_as_binary(monkeypatch, tmp_path):
    # `<title>` is deliberately routed to `RawDoc.title` rather than into the text (a
    # quote must never be citable to a tab label), so the feed needs body copy of its own.
    body = (
        b"<?xml version='1.0'?><feed><entry><title>Loom notes</title>"
        b"<summary>Thornfield Loom shipped its maintenance almanac.</summary>"
        b"</entry></feed>"
    )
    for content_type in ("application/atom+xml", "application/rss+xml", "text/xml"):
        _serve(monkeypatch, 200, {"content-type": content_type}, body)
        doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))
        assert doc is not None, content_type
        assert "maintenance almanac" in doc.text, content_type


def test_javascript_and_csv_payloads_are_text(monkeypatch, tmp_path):
    for content_type, body in (
        ("application/javascript", b"// generated\nvar people = ['Vance'];\n"),
        ("text/csv", b"name,role\nTeodoro Vance,founder\n"),
        ("application/x-ndjson", b'{"a":1}\n{"a":2}\n'),
    ):
        _serve(monkeypatch, 200, {"content-type": content_type}, body)
        doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))
        assert doc is not None, f"{content_type} is a text payload"


# --- 4. the sniffing helpers, exercised directly -------------------------------------


def test_is_binary_type_reads_the_media_type_not_the_parameters():
    assert extract_module.is_binary_type("image/png")
    assert extract_module.is_binary_type("APPLICATION/PDF; version=1.4")
    assert extract_module.is_binary_type("application/octet-stream")
    assert not extract_module.is_binary_type("text/html; charset=utf-8")
    assert not extract_module.is_binary_type("application/json")
    assert not extract_module.is_binary_type("application/vnd.github+json")
    assert not extract_module.is_binary_type("")


def test_looks_binary_needs_evidence_from_the_bytes_not_merely_a_non_ascii_char():
    assert not extract_module.looks_binary("")
    assert not extract_module.looks_binary("Zoë Fernández — €4 🎉")
    assert not extract_module.looks_binary("a\tb\r\nc\n\nd")
    assert extract_module.looks_binary("head\x00tail"), "a NUL is decisive"
    assert extract_module.looks_binary(_png().decode("utf-8", "replace"))


def test_sniff_content_type_only_fills_in_a_missing_label():
    assert extract_module.sniff_content_type('{"a": 1}', "") == "application/json"
    assert extract_module.sniff_content_type("  [1, 2]  ", "") == "application/json"
    assert extract_module.sniff_content_type("<html><p>hi</p></html>", "") == "text/html"
    assert extract_module.sniff_content_type("<?xml version='1.0'?><a/>", "") == "text/html"
    assert extract_module.sniff_content_type("just words", "") == "text/plain"
    assert (
        extract_module.sniff_content_type('{"a": 1}', "text/html; charset=utf-8")
        == "text/html; charset=utf-8"
    ), "a declared type is a fact about the response and is not second-guessed"


def test_the_module_docstring_promise_holds_for_every_shape_at_once(monkeypatch, tmp_path):
    """One assertion for the sentence this ticket restores: `fetch_text` returns None for
    a 500, a timeout, a dead host, an empty body AND a body that is not text."""
    settings = settings_for(tmp_path)

    async def raiser(self, request, **_):
        raise httpx.ReadTimeout("too slow", request=request)

    _serve(monkeypatch, 500, {"content-type": "text/html"}, b"upstream is unwell")
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", raiser)
    ratelimit_module.limiter.reset()
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None

    _serve(monkeypatch, 200, {"content-type": "text/html"}, b"")
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None

    _serve(monkeypatch, 200, {"content-type": "image/png"}, _png())
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None

    # Control: the same client still returns a document for a document.
    _serve(
        monkeypatch,
        200,
        {"content-type": "text/html"},
        b"<html><body><p>Vance chairs the loom guild.</p></body></html>",
    )
    ok = asyncio.run(fetch_text("https://payload.example.com/ok", settings=settings))
    assert ok is not None and "loom guild" in ok.text


def test_random_bytes_never_produce_a_citation(monkeypatch, tmp_path):
    """Property check over the one input class the old code turned into prose."""
    settings = settings_for(tmp_path)
    for seed in range(8):
        blob = os.urandom(600)
        _serve(monkeypatch, 200, {"content-type": "text/html"}, blob)
        doc = asyncio.run(fetch_text(f"https://payload.example.com/blob{seed}", settings=settings))
        assert doc is None, f"random blob {seed} became a RawDoc: {doc.text[:40]!r}"
