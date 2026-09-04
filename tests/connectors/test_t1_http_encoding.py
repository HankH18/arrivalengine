"""T-044: a page in a legacy encoding was discarded as binary and negatively cached.

THE DEFECT, measured before the fix.  `httpx` decodes a response whose `Content-Type`
names no charset with `default_encoding="utf-8"` and `errors="replace"`.  It never reads
`<meta charset>`.  So a real page's bytes became U+FFFD and `extract.looks_binary` rejected
them against `MAX_UNDECODABLE_RATIO = 0.10`.  Density on NATURAL PROSE -- ordinary
biographical sentences, not accent-dense cherry-picks -- read as UTF-8:

======================================  ========  =========
body                                       U+FFFD  discarded
======================================  ========  =========
Japanese Shift-JIS / EUC-JP              62 / 72%    **yes**
Chinese GBK / GB18030                         77%    **yes**
Russian cp1251 / Greek iso-8859-7         85 / 86%    **yes**
Czech iso-8859-2                          12.01%    **yes**
Turkish iso-8859-9                        10.92%    **yes**
French / Spanish / German / Portuguese  2.4-4.8%         no
PNG / PDF / ZIP / gzip / WOFF            16-50%   yes (correct)
======================================  ========  =========

Eight of twelve real pages lost.  And worse than "no document": `client._remember_non_text`
DISCARDS the body and writes a 900-second negative entry, so a caller that wanted the raw
bytes lost those too, and the mistake was then cached.

The four that survived survived CORRUPTED -- 2.4-4.8% of their characters replaced -- which
no assertion in the suite could see, because the one guard test for this
(`test_t1_http_content_type.py`) used an ASCII transliteration measuring 0.257%.  That is
T-045, and it is fixed in that file.

WHAT THESE TESTS PIN.  Two directions, and they are not symmetric:

* a document that says what codec it is in must come back INTACT, whatever the codec;
* a body that no codec explains must still be refused, because the alternative to
  discarding a PNG is quoting one.

The specimens are graded against the prose literal each one was encoded FROM, using the
stdlib's own codecs -- never against anything in `arrival.http`.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import ratelimit as ratelimit_module
from arrival.http.client import fetch_record, fetch_text
from arrival.http.extract import decode_body, detect_encoding

pytestmark = pytest.mark.ticket("T-1")

_URL = "https://encoded.example.com/profile"

# --- natural prose, one specimen per script -----------------------------------------

JAPANESE = (
    "山田太郎は東京で生まれ育ったソフトウェア技術者です。大学では計算機科学を学び、"
    "卒業後は大手の通信会社に入社しました。現在は分散システムの設計と運用を担当しており、"
    "毎日多くの利用者の要求を処理する基盤を支えています。"
)
CHINESE = (
    "王小明是一位在北京工作的软件工程师。他大学时主修计算机科学，毕业以后进入了一家大型的"
    "互联网公司，负责分布式系统的设计与维护。他每天都要处理来自世界各地用户的请求。"
)
CZECH = (
    "Jan Novák je zkušený vývojář, který se už deset let věnuje návrhu rozsáhlých "
    "distribuovaných systémů. Vystudoval informatiku na Českém vysokém učení technickém "
    "v Praze a dnes vede tým spravující infrastrukturu pro několik set tisíc uživatelů."
)
TURKISH = (
    "Ahmet Yılmaz, İstanbul'da yaşayan deneyimli bir yazılım mühendisidir. Üniversitede "
    "bilgisayar mühendisliği okudu ve dağıtık sistemlerin tasarımından sorumludur; her gün "
    "yüz binlerce kullanıcının isteğini karşılayan altyapıyı ayakta tutar."
)
GREEK = (
    "Ο Γιώργος Παπαδόπουλος είναι έμπειρος μηχανικός λογισμικού που ζει στην Αθήνα. "
    "Σπούδασε πληροφορική και ηγείται μιας ομάδας που συντηρεί υποδομή η οποία εξυπηρετεί "
    "καθημερινά εκατοντάδες χιλιάδες αιτήματα χρηστών."
)
RUSSIAN = (
    "Иван Петров — опытный инженер-программист, который уже более десяти лет живёт и "
    "работает в Москве. Он руководит командой, поддерживающей инфраструктуру, "
    "обрабатывающую сотни тысяч запросов ежедневно."
)
GERMAN = (
    "Jürgen Müller ist ein erfahrener Softwareentwickler, der seit über zehn Jahren in "
    "München arbeitet. Er leitet ein Team, das eine Infrastruktur betreut, die täglich "
    "Hunderttausende Anfragen beantwortet, und schreibt regelmäßig darüber."
)

#: `(label, prose, codec)`. Every one of these is a page a member of this club could
#: plausibly have written about themselves, in the encoding their CMS actually emits.
LEGACY_PAGES = [
    ("japanese-shift-jis", JAPANESE, "shift_jis"),
    ("japanese-euc-jp", JAPANESE, "euc_jp"),
    ("chinese-gbk", CHINESE, "gbk"),
    ("chinese-gb18030", CHINESE, "gb18030"),
    ("czech-iso-8859-2", CZECH, "iso-8859-2"),
    ("turkish-iso-8859-9", TURKISH, "iso-8859-9"),
    ("greek-iso-8859-7", GREEK, "iso-8859-7"),
    ("russian-windows-1251", RUSSIAN, "windows-1251"),
    ("german-latin-1", GERMAN, "latin-1"),
]


def _serve(monkeypatch, headers: dict[str, str], content: bytes):
    seen: list[httpx.Request] = []

    async def handle(self, request, **_):
        seen.append(request)
        return httpx.Response(200, headers=headers, content=content, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()
    return seen


def _declaring(prose: str, codec: str) -> bytes:
    """The page as its own CMS would emit it: content, plus the `<meta charset>` that
    every browser needs in order to render it at all."""
    return (
        f'<html><head><meta charset="{codec}"><title>Profil</title></head>'
        f"<body><p>{prose}</p></body></html>"
    ).encode(codec)


# --- 1. a page that declares its codec comes back intact ----------------------------


_IDS = [page[0] for page in LEGACY_PAGES]


@pytest.mark.parametrize(("label", "prose", "codec"), LEGACY_PAGES, ids=_IDS)
def test_a_page_that_declares_its_encoding_is_read_in_that_encoding(
    monkeypatch, tmp_path, label, prose, codec
):
    """The headline case, and the one the web actually serves.

    HTML5 requires a non-UTF-8 document to name its codec in the first 1024 bytes, so this
    is not an exotic shape -- it is the ONLY shape a legacy page can have and still be
    readable in a browser. Before T-044 every row here except the last came back `None`,
    and the URL was negatively cached for 900 seconds on top of that.

    The answer key is `prose`, the literal these bytes were encoded FROM by the stdlib.
    """
    _serve(monkeypatch, {"content-type": "text/html"}, _declaring(prose, codec))

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, (
        f"a {codec} page declaring `<meta charset={codec}>` came back as no document at "
        "all. It is text, it says which text, and it was thrown away as if it were a PNG"
    )
    assert prose in doc.text, (
        f"the {codec} page survived but was decoded with the wrong codec. "
        f"Got: {doc.text[:120]!r}"
    )
    assert "�" not in doc.text


@pytest.mark.parametrize(("label", "prose", "codec"), LEGACY_PAGES, ids=_IDS)
def test_an_xml_document_that_declares_its_encoding_is_read_in_that_encoding(
    monkeypatch, tmp_path, label, prose, codec
):
    """The same, declared the XML way. A feed is the one document type in this project
    that carries a date, so losing one to a codec is losing the only dateable source."""
    body = (
        f"<?xml version='1.0' encoding='{codec}'?><feed><entry>"
        f"<summary>{prose}</summary></entry></feed>"
    ).encode(codec)
    _serve(monkeypatch, {"content-type": "application/atom+xml"}, body)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, f"an atom feed declaring encoding={codec} came back as None"
    assert prose in doc.text, f"decoded with the wrong codec: {doc.text[:120]!r}"


def test_a_declaration_in_the_older_http_equiv_form_is_read_too(monkeypatch, tmp_path):
    """`<meta http-equiv="Content-Type" content="text/html; charset=...">` is what a page
    old enough to be in a legacy encoding is most likely to actually carry."""
    body = (
        '<html><head><meta http-equiv="Content-Type" '
        f'content="text/html; charset=shift_jis"></head><body><p>{JAPANESE}</p></body></html>'
    ).encode("shift_jis")
    _serve(monkeypatch, {"content-type": "text/html"}, body)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None and JAPANESE in doc.text


def test_a_byte_order_mark_is_believed_over_everything_else(monkeypatch, tmp_path):
    """A BOM is not a guess about the document, it IS the document saying so."""
    page = f"<html><body><p>{JAPANESE}</p></body></html>"
    _serve(monkeypatch, {"content-type": "text/html"}, page.encode("utf-16"))

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, "a UTF-16 page with a BOM is a text document"
    assert JAPANESE in doc.text


# --- 2. an undeclared single-byte page is read, and read correctly -------------------


def test_an_undeclared_western_european_page_round_trips_exactly(monkeypatch, tmp_path):
    """This page was never DROPPED -- it was silently CORRUPTED, which no assertion saw.

    German latin-1 prose measures 2.39% U+FFFD read as UTF-8, comfortably under the 10%
    threshold, so the old code returned a `RawDoc` and called it a success. Every umlaut
    in it had been replaced. T-3 can quote that text and T-7 can display it.
    """
    page = f"<html><body><p>{GERMAN}</p></body></html>"
    _serve(monkeypatch, {"content-type": "text/html"}, page.encode("latin-1"))

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None
    assert GERMAN in doc.text, f"an undeclared latin-1 page lost its accents: {doc.text[:120]!r}"


def test_an_undeclared_central_european_page_is_kept_rather_than_discarded(
    monkeypatch, tmp_path
):
    """Czech iso-8859-2 measures 12.01% U+FFFD read as UTF-8 -- over the threshold, so the
    whole page was discarded and the URL negatively cached.

    With nothing in the document naming a codec there is no way to recover the exact
    diacritics, and this test does not pretend otherwise: it pins that the DOCUMENT
    survives, which is the difference between a source with a wrong accent and no source.
    """
    marker = "Vystudoval informatiku na "
    page = f"<html><body><p>{CZECH}</p></body></html>"
    _serve(monkeypatch, {"content-type": "text/html"}, page.encode("iso-8859-2"))

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, (
        "an undeclared iso-8859-2 page is a text document at 12% replacement characters; "
        "discarding it loses the page AND caches the mistake for 900 seconds"
    )
    assert marker in doc.text, f"the ASCII runs of the page must survive: {doc.text[:120]!r}"


def test_a_header_charset_that_cannot_decode_its_own_body_is_not_believed(
    monkeypatch, tmp_path
):
    """`Content-Type: text/html; charset=utf-8` over a Shift-JIS body.

    A stated charset that cannot decode its own bytes is a misconfiguration, not evidence,
    and believing it produces exactly the U+FFFD wall that got the page thrown away. The
    document's own `<meta>` is the better witness and is used instead.
    """
    _serve(
        monkeypatch,
        {"content-type": "text/html; charset=utf-8"},
        _declaring(JAPANESE, "shift_jis"),
    )

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, "the page declared shift_jis in its own markup and is readable"
    assert JAPANESE in doc.text


def test_a_header_charset_that_fits_is_still_believed(monkeypatch, tmp_path):
    """The other direction: a header that CAN decode the body wins, because it is a fact
    the origin stated about this response and outranks anything inferred."""
    page = f"<html><body><p>{RUSSIAN}</p></body></html>"
    _serve(
        monkeypatch,
        {"content-type": "text/html; charset=windows-1251"},
        page.encode("windows-1251"),
    )

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None and RUSSIAN in doc.text


# --- 3. the other direction: binary is still refused ---------------------------------


def _png(padding: int = 800) -> bytes:
    return (
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000100000001008060000001ff3ff61")
        + bytes(range(256)) * (padding // 256 + 1)
    )


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("png", _png()),
        ("pdf", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + bytes(range(256)) * 3),
        ("zip", b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + bytes(range(256)) * 3),
        ("gzip", b"\x1f\x8b\x08\x00" + bytes(range(256)) * 3),
        ("woff", b"wOFF\x00\x01\x00\x00" + bytes(range(256)) * 3),
        ("high-bytes-only", bytes(range(0x80, 0x100)) * 20),
    ],
    ids=["png", "pdf", "zip", "gzip", "woff", "high-bytes-only"],
)
def test_binary_mislabelled_as_html_is_still_refused_after_the_encoding_fix(
    monkeypatch, tmp_path, label, body
):
    """The constraint on the T-044 fix, stated as a test: reading a page's own charset
    declaration must not buy legacy text back by admitting PNGs.

    `high-bytes-only` is the adversarial row -- a body with no NUL and no control bytes at
    all, which is the shape a "just decode it as latin-1" fix would wave straight through.
    """
    _serve(monkeypatch, {"content-type": "text/html"}, body)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is None, (
        f"a {label} body served as text/html produced a RawDoc: "
        f"{getattr(doc, 'text', '')[:60]!r}"
    )


def test_binary_that_falsely_declares_a_charset_is_still_refused(monkeypatch, tmp_path):
    """A declaration is read from the head of the body, so a body can carry one by
    accident or by malice. It buys nothing: the bytes still get the last word."""
    body = b'<meta charset="shift_jis">' + _png()
    _serve(monkeypatch, {"content-type": "text/html"}, body)

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None
    assert asyncio.run(fetch_record(_URL, settings=settings_for(tmp_path))) is None


# --- 4. the helpers, exercised directly ----------------------------------------------


def test_detect_encoding_prefers_valid_utf8_over_a_stale_declaration():
    """A UTF-8 page still carrying `<meta charset=iso-8859-1>` from a decade ago is one of
    the commonest shapes on the web. Real non-UTF-8 prose is essentially never valid UTF-8,
    so trusting the bytes here costs nothing and fixes that page."""
    body = f'<meta charset="iso-8859-1"><p>{GERMAN}</p>'.encode()

    assert detect_encoding(body) == "utf-8"
    assert GERMAN in decode_body(body, None)


def test_detect_encoding_refuses_a_declaration_that_cannot_decode_the_body():
    """`<meta charset="utf-8">` over cp1252 bytes: the declaration does not fit, so it is
    worth no more than no declaration at all."""
    body = '<meta charset="utf-8"><p>Jürgen Müller</p>'.encode("cp1252")

    assert detect_encoding(body) != "utf-8"
    assert "Jürgen Müller" in decode_body(body, None)


def test_detect_encoding_reads_a_bom_before_anything_else():
    import codecs

    assert detect_encoding(codecs.BOM_UTF8 + b"hello") == "utf-8-sig"
    assert detect_encoding("hello".encode("utf-16")) == "utf-16"
    assert detect_encoding("hello".encode("utf-32")) == "utf-32"


def test_detect_encoding_leaves_undecodable_bodies_as_utf8_so_they_are_still_judged():
    """The fallback must FAIL SAFE. A body no codec explains keeps the old behaviour --
    UTF-8 with replacement characters -- so `looks_binary` still gets to refuse it."""
    assert detect_encoding(_png()) == "utf-8"
    assert detect_encoding(JAPANESE.encode("shift_jis")) == "utf-8", (
        "undeclared multi-byte prose is genuinely ambiguous; guessing a codec for it would "
        "produce confident gibberish, which is worse than the honest rejection"
    )
    assert detect_encoding(b"") == "utf-8"


def test_decode_body_never_raises_on_a_nonsense_charset():
    """A cache read and a fetch have both promised never to raise. An origin can send
    `charset=` anything, including nothing Python has ever heard of."""
    for charset in ("", "unicode-1-1", "none", "utf8mb4", "x-user-defined", "☃"):
        assert decode_body(b"plain words", charset) == "plain words"
    assert decode_body(b"", "utf-8") == ""
