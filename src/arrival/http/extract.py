"""The "light extractor": HTML/XML -> plain text, plus the RawDoc text budget.

`RawDoc.text` is the substrate every later stage stands on. T-3 verifies a quote by
substring-matching `normalize_ws(quote) in normalize_ws(doc.text)` (DESIGN Decision 5), so
whatever this module drops can never be cited afterwards, and whatever it keeps as markup
becomes a quote full of angle brackets. Hence: tags out, entities decoded, block structure
preserved as newlines, and script/style payloads discarded entirely — a page's inline
JavaScript is not something a host should ever read out loud.

Stdlib only. `html.parser` ships with CPython; adding a dependency for this is not
allowed in this ticket and is not worth it for one page shape.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

__all__ = [
    "MAX_CONTROL_RATIO",
    "MAX_TEXT_CHARS",
    "MAX_UNDECODABLE_RATIO",
    "SNIFF_CHARS",
    "clip",
    "html_title",
    "html_to_text",
    "is_binary_type",
    "json_to_text",
    "looks_binary",
    "looks_like",
    "sniff_content_type",
]

#: DESIGN §Interfaces: "extracted plain text, <= 20k chars, never empty".
MAX_TEXT_CHARS = 20_000

#: Elements whose character data is machinery, not prose.
_DROP_CONTENT = frozenset({"script", "style", "noscript", "template", "svg", "canvas"})

#: Elements whose character data is page FURNITURE, not prose about the subject.
#:
#: This is the difference between a citation and a joke. `RawDoc.text` is what T-3 quotes
#: verbatim (`normalize_ws(quote) in normalize_ws(doc.text)`, DESIGN Decision 5), so
#: anything kept here is something a host can end up reading out loud. Measured on the
#: recorded corpus before this existed: the `self_page` document for a member's own site
#: began "Team | Subscribe | Press | We use cookies to improve your experience." -- which
#: satisfies "non-empty text" and every other assertion in the contract, and is worthless.
_DROP_CHROME = frozenset({"nav", "footer"})

#: ARIA landmarks for the same three regions, for pages that use `<div role=...>`.
_CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo"})

#: Consent/cookie notices, by class or id. Deliberately narrow: these three words are
#: never part of an article about a person, whereas a looser pattern (`banner`, `notice`,
#: `modal`) would start eating real content.
_CHROME_ATTR = re.compile(r"cookie|consent|gdpr", re.IGNORECASE)

#: Elements that never get an end tag, so they must not go on the open-element stack.
_VOID = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }
)

#: Elements that imply a line break around their content.
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)

_MANY_NEWLINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t\r\f\v]+")


def _is_noise(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
    """True when this element opens a region whose text is machinery or furniture."""
    if tag in _DROP_CONTENT or tag in _DROP_CHROME:
        return True
    for name, value in attrs:
        if not value:
            continue
        if name == "role" and value.strip().lower() in _CHROME_ROLES:
            return True
        if name in ("class", "id") and _CHROME_ATTR.search(value):
            return True
    return False


class _TextExtractor(HTMLParser):
    """Collect visible text and the document title.

    `convert_charrefs=True` (the default) is what turns `&amp;` back into `&` before the
    text ever reaches us, so entity decoding is not a separate pass.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str = ""
        # An open-element stack rather than a counter. A counter can only be decremented
        # by a matching end tag, so ONE unclosed <svg> -- an icon in a page header is the
        # common case -- suppressed every remaining character in the document and the page
        # came back empty. With a stack, any enclosing close tag ends the suppression.
        self._stack: list[str] = []
        self._suppress_at: int | None = None
        self._in_title = False

    @property
    def _suppressed(self) -> bool:
        return self._suppress_at is not None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag not in _VOID:
            self._stack.append(tag)
            if not self._suppressed and _is_noise(tag, attrs):
                self._suppress_at = len(self._stack) - 1
        if not self._suppressed and tag in _BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<foo/>` opens and closes in one token, so it never enters the stack and a
        # self-closing drop tag suppresses nothing after itself.
        if not self._suppressed and tag.lower() in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in self._stack:
            # Unwind to the matching open tag, discarding anything left unclosed inside it.
            while self._stack:
                popped = self._stack.pop()
                if self._suppress_at is not None and len(self._stack) <= self._suppress_at:
                    self._suppress_at = None
                if popped == tag:
                    break
        if not self._suppressed and tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppressed:
            return
        if self._in_title:
            self.title += data
            # A <title> is chrome, not body copy: it is returned separately and is not
            # repeated into the text, so a quote can never be "cited" to the tab label.
            return
        self.parts.append(data)


def _parse(markup: str) -> _TextExtractor:
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup is data, never an exception path
        pass
    return parser


def _tidy(raw: str) -> str:
    lines = [_SPACES.sub(" ", line).strip() for line in raw.split("\n")]
    return _MANY_NEWLINES.sub("\n\n", "\n".join(lines)).strip()


def html_to_text(markup: str) -> str:
    """Visible text of an HTML/XML document, with block structure kept as newlines."""
    return _tidy("".join(_parse(markup).parts))


def html_title(markup: str) -> str:
    """The document `<title>`, whitespace-collapsed. Empty string when there is none."""
    return _SPACES.sub(" ", _parse(markup).title).strip()


def json_to_text(body: str) -> str:
    """JSON passthrough (TASKS T-1 acceptance 1).

    Re-serialised with indentation when it parses, so the text a human (or an LLM) reads
    out of `RawDoc.text` has line structure rather than being one 4kB line; returned
    verbatim when it does not parse, because an unparseable body is still evidence.
    """
    try:
        return json.dumps(json.loads(body), indent=2, ensure_ascii=False, sort_keys=False)
    except (ValueError, TypeError):
        return body.strip()


def looks_like(content_type: str, kind: str) -> bool:
    """True when `content_type` names `kind` ("json", "html", "xml", ...)."""
    return kind in content_type.split(";")[0].strip().lower()


# --- is this body a text document at all? (T-026) -----------------------------------
#
# `client.py`'s docstring promises that "a body that is not text" is `None`. It was not:
# `httpx` decodes any body with `errors="replace"`, so a PNG came back as a string full of
# U+FFFD and became a `RawDoc` a host could be asked to read out loud. Two independent
# checks, because the two failure directions are not symmetric.

#: Media types whose payload is never a text document. Matched on the media type only, so
#: `application/pdf; version=1.4` is caught and `application/vnd.github+json` is not --
#: the `+json` / `+xml` structured-syntax suffixes are checked FIRST, because a rule that
#: matched `application/vnd.` would delete every GitHub response the connector makes.
_BINARY_TYPE_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "font/",
    "model/",
    "application/vnd.ms-",
    "application/vnd.openxmlformats-",
    "application/vnd.oasis.opendocument.",
    "application/x-font",
)
_BINARY_TYPES = frozenset(
    {
        "application/pdf",
        "application/zip",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
        "application/x-tar",
        "application/octet-stream",
        "application/msword",
        "application/vnd.rar",
        "application/epub+zip",
        "application/wasm",
        "application/x-protobuf",
        "application/protobuf",
        "application/x-shockwave-flash",
        "application/java-archive",
        "application/postscript",
    }
)

#: Structured-syntax suffixes: whatever the vendor tree says, these are text.
_TEXTUAL_SUFFIXES = ("+json", "+xml", "+yaml", "+text")

#: How much of a body to inspect. A document that is text for its first few kilobytes and
#: binary afterwards is not a shape any of these sources produce, and reading a 20MB body
#: character by character to find that out is a cost with no payer.
SNIFF_CHARS = 4096

#: Fraction of the sample that may be U+FFFD before the body is called binary. Measured:
#: bytes that are not text decode to 40-60% replacement characters, while a latin-1 page
#: read as UTF-8 (a lossy read of a REAL text document, which must survive) sits at a few
#: percent. Ten percent separates them with room on both sides.
MAX_UNDECODABLE_RATIO = 0.10

#: Fraction of the sample that may be C0/C1 control characters. Real prose has none
#: outside the whitespace set; binary is ~11% by construction.
MAX_CONTROL_RATIO = 0.02

_TEXT_WHITESPACE = frozenset("\t\n\r\f\v")


def is_binary_type(content_type: str) -> bool:
    """True when this media type names a payload that is not a text document."""
    media = content_type.split(";")[0].strip().lower()
    if not media:
        return False
    if media.endswith(_TEXTUAL_SUFFIXES):
        return False
    if media.startswith("text/"):
        return False
    return media in _BINARY_TYPES or media.startswith(_BINARY_TYPE_PREFIXES)


def looks_binary(body: str) -> bool:
    """True when this decoded body is evidently not text, whatever its label claimed.

    A label is a CLAIM by the origin, and the case a label-only check misses is the common
    one: a CDN answering `text/html` for everything it does not recognise. So the decoded
    characters get the last word. An empty body is not binary -- it is empty, which is a
    different rejection with a different reason attached to it.
    """
    if not body:
        return False
    sample = body[:SNIFF_CHARS]
    if "\x00" in sample:
        # Decisive on its own: NUL is legal UTF-8 and appears in essentially every binary
        # container, and in no document anyone means to publish.
        return True
    undecodable = sample.count("�")
    if undecodable > len(sample) * MAX_UNDECODABLE_RATIO:
        return True
    control = sum(
        1
        for character in sample
        if (character < " " and character not in _TEXT_WHITESPACE) or character == "\x7f"
    )
    return control > len(sample) * MAX_CONTROL_RATIO


def sniff_content_type(body: str, declared: str) -> str:
    """`declared` when the response labelled itself; otherwise a type read off the body.

    RESOLVED ONCE, AT FETCH TIME, and stored -- not re-derived on every read. The cache
    keeps the response's content type, so a body sniffed on the way in and re-sniffed on
    the way out could be extracted two different ways in the same build; that is the bug
    a "just sniff it lazily" version has.

    The old default was `text/html`, which ran unlabelled JSON through the HTML extractor.
    Measured: `{"expr": "a<b and c>d"}` came back as `{"expr": "ad"}` -- nine characters
    deleted from inside a string VALUE, and the document no longer parsed as itself.
    """
    if declared.split(";")[0].strip():
        return declared
    head = body.lstrip()[:1]
    if head in ("{", "["):
        try:
            json.loads(body)
        except (ValueError, TypeError):
            pass
        else:
            return "application/json"
    if head == "<":
        return "text/html"
    return "text/plain"


def clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Trim to the RawDoc text budget on a word boundary where one is close enough."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind(" ")
    # Only honour the word boundary if it is not throwing away a meaningful tail.
    if cut > limit - 200:
        head = head[:cut]
    return head.rstrip()
