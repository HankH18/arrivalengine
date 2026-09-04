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

import codecs
import json
import re
from html.parser import HTMLParser

__all__ = [
    "MAX_CONTROL_RATIO",
    "MAX_LATIN_HIGH_BYTE_RATIO",
    "MAX_TEXT_CHARS",
    "MAX_UNDECODABLE_RATIO",
    "SNIFF_BYTES",
    "SNIFF_CHARS",
    "clip",
    "decode_body",
    "detect_encoding",
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
        # T-039: only the FIRST <title> is the document's own title. See `handle_data`.
        self._title_taken = False

    @property
    def _suppressed(self) -> bool:
        return self._suppress_at is not None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title" and not self._title_taken and not self._suppressed:
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
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_taken = True
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
            #
            # T-039: only the FIRST one. An HTML document has exactly one <title> and its
            # behaviour here is unchanged; an XML FEED has one per entry plus one for the
            # channel, and routing all of them to `RawDoc.title` extracted an Atom document
            # whose entries carry only headlines to the EMPTY STRING -- `fetch_text` then
            # returned None for a document that was entirely readable. The first <title> is
            # still the document's own (the channel's), so nothing may be cited to a tab
            # label; every later one is body copy of a separate item and belongs in the text.
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

#: Fraction of the sample that may be U+FFFD before the body is called binary.
#:
#: THIS IS A SAFETY NET, NOT THE ENCODING POLICY -- and it was load-bearing until T-044,
#: which is how it came to be wrong. The comment that stood here read: "a latin-1 page read
#: as UTF-8 sits at a few percent. Ten percent separates them with room on both sides."
#: That is true only of the Western European languages it was tested against. Measured on
#: natural prose read as UTF-8 after the origin declined to name a charset:
#:
#:     French 4.79%   Spanish 2.55%   German 2.39%   Portuguese 2.86%   -- survived
#:     Turkish iso-8859-9 10.92%      Czech iso-8859-2 12.01%           -- DROPPED
#:     Japanese Shift-JIS 62.29%      Chinese GBK 77.29%                -- DROPPED
#:     Russian cp1251 84.96%          Greek iso-8859-7 85.62%           -- DROPPED
#:
#: Eight of twelve real pages were discarded as binary and negatively cached for 900s.
#: No threshold fixes that, because the ratio was measuring the wrong thing: a decode
#: already known to have used the wrong codec. `decode_body` now reads the document's own
#: declaration first, so a body still full of U+FFFD by the time it reaches here is one
#: nothing could decode -- and that is what this ratio is for.
MAX_UNDECODABLE_RATIO = 0.10

#: Fraction of the sample that may be C0/C1 control characters. Real prose has none
#: outside the whitespace set; binary is ~11% by construction.
MAX_CONTROL_RATIO = 0.02

_TEXT_WHITESPACE = frozenset("\t\n\r\f\v")

#: The same set as bytes, for the checks that run BEFORE anything has been decoded.
_WHITESPACE_BYTES = frozenset(b"\t\n\r\f\v")


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


# --- what codec are these bytes in? (T-044) ------------------------------------------
#
# THE DEFECT.  `httpx` decodes a response whose header names no charset with
# `default_encoding="utf-8"` and `errors="replace"`.  It never reads `<meta charset>`, so a
# perfectly ordinary page in a legacy encoding arrived as a wall of U+FFFD and `looks_binary`
# rejected it -- and `client._remember_non_text` then DISCARDED THE BODY and wrote a 900s
# negative entry, so the caller lost the bytes too.  See `MAX_UNDECODABLE_RATIO` for the
# measured table; eight of twelve real pages were lost, and the four that survived (French,
# Spanish, German, Portuguese) survived CORRUPTED -- 2-5% of their characters replaced.
#
# THE FIX is to stop guessing from a decode and read what the document says about itself,
# in the order a browser does: a byte-order mark is decisive, then the document's own
# declaration, and only then a fallback.  Everything here is stdlib; no dependency is added.

#: How many bytes to scan for a declaration and to sample for the fallback heuristics.
#: HTML5 requires `<meta charset>` inside the first 1024 bytes; this is generous about
#: badly-ordered `<head>` blocks without reading a whole 10MB body twice.
SNIFF_BYTES = 4096

#: BOMs, longest first: `FF FE 00 00` (UTF-32 LE) starts with `FF FE` (UTF-16 LE), so
#: testing the shorter one first would decode a UTF-32 document as UTF-16 and produce
#: exactly the mojibake this function exists to prevent.
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

#: The two ways a document names its own codec, in one pass over the head of the body:
#: an XML declaration's `encoding=`, and HTML's `<meta charset=>` -- which also catches the
#: older `<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">`, because
#: `[^>]*?` crosses into the `content` attribute's value and stops at its quote.
_CHARSET_IN_MARKUP = re.compile(
    rb"""(?:
          <\?xml[^>]{0,400}?encoding\s*=\s*["']([A-Za-z0-9_.:+-]{1,40})["']
        | <meta[^>]{0,400}?charset\s*=\s*["']?\s*([A-Za-z0-9_.:+-]{1,40})
    )""",
    re.IGNORECASE | re.VERBOSE,
)

#: Fraction of a sample's bytes that may be >= 0x80 for it to be read as SINGLE-BYTE latin
#: text when nothing declared a codec. Measured on the same prose as `MAX_UNDECODABLE_RATIO`:
#:
#:     latin-1 French 4.79%  Spanish 2.55%  German 2.39%  Portuguese 2.86%
#:     iso-8859-9 Turkish 10.92%   iso-8859-2 Czech 12.44%      <- the top of the class
#:     Shift-JIS 86.96%  EUC-JP 100%  GBK 100%  cp1251 85.04%  iso-8859-7 85.62%
#:     PDF 20.83%  ZIP 34.94%  PNG 47.60%  WOFF 49.88%  gzip 55.66%
#:
#: Twenty percent sits above every single-byte page measured and below every other class.
#: It is a statement about the BYTES, not a guess at a codec, and it fails safe: a body
#: over it keeps the old behaviour exactly (decoded as UTF-8, judged by `looks_binary`).
MAX_LATIN_HIGH_BYTE_RATIO = 0.20

#: WHATWG's fallback for unlabelled content, and a strict superset of latin-1 over the
#: 0xA0-0xFF range every Western European page actually uses -- so a latin-1 page with no
#: declaration round-trips EXACTLY (verified on French, Spanish, German and Portuguese
#: prose). An iso-8859-2 or -9 page read this way gets the wrong accent on some letters,
#: which is what a browser does with it too, and is a document rather than a deletion.
_LATIN_FALLBACK = "cp1252"


def _known_encoding(name: str | None) -> str | None:
    """`name` when Python has a codec for it, else None. Never raises."""
    if not name:
        return None
    try:
        codecs.lookup(name)
    except (LookupError, TypeError, ValueError):
        return None
    return name


def _decodes_strictly(content: bytes, encoding: str) -> bool:
    """True when every byte of `content` is legal in `encoding`.

    A declaration that cannot decode its own document is worth no more than no declaration:
    `<meta charset="utf-8">` on a cp1252 page is one of the commonest misconfigurations on
    the web, and believing it produces exactly the U+FFFD wall T-044 is about.
    """
    try:
        content.decode(encoding)
    except (UnicodeDecodeError, LookupError, ValueError):
        return False
    return True


def detect_encoding(content: bytes) -> str:
    """The codec these bytes are most likely in, for a response that named none.

    Deliberately shaped to be usable as `httpx.AsyncClient(default_encoding=...)`, which
    takes a `Callable[[bytes], str]`, so any read of `Response.text` gets this policy too.

    The order is the one that loses the least when it is wrong:

    1. **A BOM** is not a guess, it is the document saying so in its first bytes.
    2. **Valid UTF-8** wins over any declaration. Real non-UTF-8 prose is essentially never
       valid UTF-8, while a UTF-8 page that still declares `iso-8859-1` in a stale `<meta>`
       is common -- so trusting the bytes here fixes that case and costs nothing.
    3. **The document's own declaration**, if Python knows the codec AND it decodes the
       whole body without error.
    4. **Single-byte latin**, but only for a body whose bytes look like it: no NUL, few
       control bytes, few high bytes (`MAX_LATIN_HIGH_BYTE_RATIO`).
    5. **UTF-8 otherwise** -- i.e. replacement characters and the old behaviour, which is
       the right answer for a body no codec can explain. `looks_binary` still judges it.
    """
    if not content:
        return "utf-8"

    for bom, encoding in _BOM_ENCODINGS:
        if content.startswith(bom):
            return encoding

    if _decodes_strictly(content, "utf-8"):
        return "utf-8"

    match = _CHARSET_IN_MARKUP.search(content[:SNIFF_BYTES])
    if match is not None:
        declared = (match.group(1) or match.group(2) or b"").decode("ascii", "ignore")
        known = _known_encoding(declared)
        if known is not None and _decodes_strictly(content, known):
            return known

    sample = content[:SNIFF_BYTES]
    if b"\x00" in sample:
        # Binary, and `looks_binary` is the function that gets to say so. Reading it as
        # latin text would hide the NUL behind a printable character.
        return "utf-8"
    controls = sum(
        1 for byte in sample if (byte < 0x20 and byte not in _WHITESPACE_BYTES) or byte == 0x7F
    )
    if controls > len(sample) * MAX_CONTROL_RATIO:
        return "utf-8"
    high = sum(1 for byte in sample if byte >= 0x80)
    if high <= len(sample) * MAX_LATIN_HIGH_BYTE_RATIO:
        return _LATIN_FALLBACK
    return "utf-8"


def decode_body(content: bytes, declared_charset: str | None = None) -> str:
    """`content` as text, believing the response's own `charset=` when it fits the bytes.

    `declared_charset` is the `charset` parameter of the `Content-Type` header -- a fact the
    origin stated, so it wins whenever it can actually decode the document. When it cannot
    (a header claiming UTF-8 over a Shift-JIS body is the case that reaches here) it is
    discarded in favour of `detect_encoding`, because a stated charset that does not fit its
    own bytes is a misconfiguration and not evidence.

    `errors="replace"` remains the last resort, so this function never raises and a body
    nothing can decode still arrives as the U+FFFD wall `looks_binary` is there to reject.
    """
    if not content:
        return ""
    named = _known_encoding(declared_charset)
    if named is not None and _decodes_strictly(content, named):
        return content.decode(named, errors="replace")
    return content.decode(detect_encoding(content), errors="replace")


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
