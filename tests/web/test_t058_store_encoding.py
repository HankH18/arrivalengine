"""T-058: a dossier that is not UTF-8 must abort boot with its PATH, not a decode traceback.

`DossierStore._read_one` read every dossier under `except OSError`, and that handler's
comment claimed it covered "bad encoding". It did not. `UnicodeDecodeError` subclasses
`ValueError`, not `OSError`, so a dossier saved as latin-1 escaped `DossierLoadError`
entirely.

Why that is a boot defect rather than a cosmetic one: `arrival.web.app` ends with
`app = create_app()`, so the corpus is read at IMPORT. Measured on this branch before the
fix, with one latin-1 file in `DOSSIER_DIR`::

    $ uv run pytest -q
    ERROR tests/web/test_t8_app.py - UnicodeDecodeError: 'utf-8' codec can't decode ...
    !!!!! Interrupted: 1 error during collection !!!!!
    exit 2   # zero of the project's 1329 tests ran

Boot still aborts after the fix, and that is correct -- README §Deploy and this module's
own subject (`store.py`'s docstring, `app.py:133`) both say a bad dossier fails the import
loudly. What the fix changes is WHICH exception comes out: `DossierLoadError` naming the
file an operator has to go look at, instead of a codec's byte offset.

**What these grade against, none of which this ticket may write:**

* the CPython exception hierarchy (`UnicodeDecodeError` is a `ValueError`) -- the stdlib
  fact the old handler got wrong, pinned here so a future "simplification" back to
  `except OSError` fails rather than silently re-opening the hole;
* `tests/fixtures/dossiers/alpha.json`, T-0's corpus, for the good file in a mixed corpus;
* byte-string literals written in this module, which are the whole input;
* a subprocess's exit status and stderr, for the import-time blast radius.

No assertion here compares against the message wording in `store.py`; the contract being
graded is "a `RuntimeError` subclass whose text names the offending path", which is what
`app.py:133` and README §Deploy state independently of this ticket.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from arrival.web.store import DossierLoadError, DossierStore

pytestmark = pytest.mark.ticket("T-8")

ROOT = Path(__file__).resolve().parents[2]
GOOD_FIXTURE = ROOT / "tests" / "fixtures" / "dossiers" / "alpha.json"

#: A dossier-shaped payload whose only non-ASCII lives in a person's name. Written as text
#: here and encoded per-test, so each case differs only in its codec.
#:
#: `ensure_ascii=False` is load-bearing and is not stylistic. The default `True` escapes
#: `ü` to the ASCII sequence `ü`, so `DOSSIER_TEXT.encode("latin-1")` produced pure
#: ASCII, decoded as UTF-8 without complaint, and reached pydantic -- i.e. the latin-1 and
#: cp1252 cases below tested nothing at all. The `__cause__` assertion is what caught it.
DOSSIER_TEXT = json.dumps(
    {"person": {"person_id": "jurgen-muller", "name": "Jürgen Müller", "details": ["founder"]}},
    ensure_ascii=False,
)


def test_the_stdlib_puts_unicodedecodeerror_under_valueerror_not_oserror():
    """The fact the old handler was wrong about, and the reason `except OSError` was a bug.

    This has no dependency on `arrival` at all. It exists so that reverting `_read_one` to
    `except OSError` cannot be defended as "the comment says it covers bad encoding" -- the
    comment was wrong, and here is the interpreter saying so.
    """
    assert issubclass(UnicodeDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, OSError)
    assert not issubclass(ValueError, OSError)


@pytest.mark.parametrize(
    ("codec", "why"),
    [
        ("latin-1", "the commonest way a hand-edited JSON file leaves an editor on Windows"),
        ("cp1252", "what 'ANSI' means in Notepad's save dialog"),
        ("utf-16", "a BOM plus NUL bytes, which fails at position 0 rather than mid-string"),
    ],
)
def test_a_dossier_in_the_wrong_codec_aborts_boot_naming_its_path(tmp_path, codec, why):
    """T-8 acceptance 1 extended to the encoding axis: fail loudly, and say which file."""
    corpus = tmp_path / "dossiers"
    corpus.mkdir()
    (corpus / "jurgen.json").write_bytes(DOSSIER_TEXT.encode(codec))

    with pytest.raises(DossierLoadError) as excinfo:
        DossierStore.load(corpus)

    exc = excinfo.value
    assert "jurgen.json" in str(exc), (
        f"{codec} ({why}) aborted boot without naming the file to go look at: {exc}"
    )
    assert isinstance(exc, RuntimeError)
    assert not isinstance(exc, UnicodeDecodeError), (
        "the raw decode error escaped instead of being wrapped"
    )
    assert isinstance(exc.__cause__, UnicodeDecodeError), (
        "the cause chain must keep the codec detail an operator needs, "
        f"got {type(exc.__cause__).__name__}"
    )


def test_the_bad_file_is_named_even_when_it_sits_among_good_ones(tmp_path):
    """A corpus is many files; "some dossier is invalid" is not an error anyone can act on.

    The good file is T-0's committed `alpha.json`, copied in rather than used in place.
    """
    corpus = tmp_path / "dossiers"
    corpus.mkdir()
    (corpus / "alpha.json").write_bytes(GOOD_FIXTURE.read_bytes())
    assert len(DossierStore.load(corpus)) == 1, "the good fixture alone must still load"

    (corpus / "zz-broken.json").write_bytes(DOSSIER_TEXT.encode("latin-1"))

    with pytest.raises(DossierLoadError) as excinfo:
        DossierStore.load(corpus)

    message = str(excinfo.value)
    assert "zz-broken.json" in message
    assert "alpha.json" not in message, f"the innocent file was blamed: {message}"


def test_a_utf8_byte_order_mark_is_also_a_named_error_rather_than_a_traceback(tmp_path):
    """A BOM decodes fine and then breaks `json.loads`, so it exercises the next arm down.

    Included because it is the near-miss: a reader who fixed only the decode arm would still
    be fine here, and a reader who broke the JSON arm would not be. Either way the operator
    gets a path.
    """
    corpus = tmp_path / "dossiers"
    corpus.mkdir()
    (corpus / "bommed.json").write_bytes(b"\xef\xbb\xbf" + DOSSIER_TEXT.encode("utf-8"))

    with pytest.raises(DossierLoadError) as excinfo:
        DossierStore.load(corpus)
    assert "bommed.json" in str(excinfo.value)


def test_a_valid_utf8_dossier_with_non_ascii_text_still_loads(tmp_path):
    """The fix must widen the handler, not the file-reading path: real UTF-8 still works."""
    corpus = tmp_path / "dossiers"
    corpus.mkdir()
    (corpus / "alpha.json").write_bytes(GOOD_FIXTURE.read_bytes())

    store = DossierStore.load(corpus)

    assert len(store) == 1
    assert store.resolve("alpha") == "alpha"


def test_importing_the_web_app_over_a_bad_corpus_gives_the_diagnosis_not_a_decode_error(
    tmp_path,
):
    """The blast radius, out of process because `arrival.web.app` is already imported here.

    `app = create_app()` at module scope means the corpus loads at import time, so this is
    exactly what `uvicorn arrival.web.app:app` and every pytest collection of a web test
    module do. Before the fix the terminating line was `UnicodeDecodeError: ...`; after it,
    `DossierLoadError` with the path. The import failing at all is the specified behaviour
    (README §Deploy) and is not what this asserts.
    """
    corpus = tmp_path / "dossiers"
    corpus.mkdir()
    (corpus / "jurgen.json").write_bytes(DOSSIER_TEXT.encode("latin-1"))

    env = {**os.environ, "DOSSIER_DIR": str(corpus)}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r});"
         " import arrival.web.app"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=120,
    )

    assert result.returncode != 0, (
        "a corpus that cannot be read must not boot into a half-empty roster:\n"
        f"{result.stdout}"
    )
    stderr = result.stderr
    assert "DossierLoadError" in stderr, f"boot failed without the diagnosis:\n{stderr}"
    assert "jurgen.json" in stderr, f"the failure did not name the file:\n{stderr}"
    final_line = stderr.strip().splitlines()[-1]
    assert not final_line.startswith("UnicodeDecodeError"), (
        f"the raw decode error is still what reaches the operator:\n{stderr}"
    )
