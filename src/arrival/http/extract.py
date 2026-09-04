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

__all__ = ["MAX_TEXT_CHARS", "clip", "html_title", "html_to_text", "json_to_text", "looks_like"]

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
