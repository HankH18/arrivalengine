"""T-9 acceptance 1: the committed dossier corpus is real, valid and fully cited.

This module validates whatever is committed at ``data/dossiers/``. It is deliberately a
VALIDATOR, not a placeholder: it loads every committed dossier, validates it against
``arrival.contracts.Dossier``, and for every fact that ``arrival.taste.is_displayable``
says may reach a screen, re-checks the citation the whole product rests on —
``normalize_ws(fact.provenance.quote)`` must be a substring of ``normalize_ws(doc.text)``
for the RawDoc committed at ``data/docs/{doc_id}.json`` (DESIGN Decision 5, SPEC C8, S6).

**Why it skips rather than passes on an empty corpus.** Populating ``data/dossiers/`` is a
HUMAN GATE — it needs the live-network build of the ten real roster people (an Anthropic
key and a search account) followed by a fact-by-fact human review of every displayed fact
at its source URL. Neither exists inside an autonomous build, so this module cannot make
the corpus. What it must never do is go GREEN on the absence of one: a validator that
passes on an empty directory lets the human gate be skipped in silence, and the submission
ships uncited claims about real people. So the corpus is a module fixture that ``skip``s,
with a reason that names the gate, and every check below inherits that skip.

The moment the human build is committed these become live, failing-capable checks over
real data — no edit here is needed to "turn them on".

Layout, from DESIGN §Data models and T-9 acceptance 1, resolved from THIS file rather than
from the working directory so the checks measure the repository they live in and not
wherever the runner happened to start:

    data/dossiers/{person_id}.json   one Dossier each
    data/docs/{doc_id}.json          the RawDoc every provenance cites
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from arrival.contracts import Dossier, RawDoc
from arrival.taste import is_displayable
from arrival.util import doc_id as doc_id_for_url
from arrival.util import normalize_ws, slug

pytestmark = pytest.mark.ticket("T-9")

#: Resolved from ``__file__``: ``tests/`` sits directly under the repository root, and a
#: cwd-relative path would silently validate a different (usually empty) corpus depending
#: on where pytest was invoked from.
REPO_ROOT = Path(__file__).resolve().parents[1]
DOSSIER_DIR = REPO_ROOT / "data" / "dossiers"
DOCS_DIR = REPO_ROOT / "data" / "docs"

#: R16 / T-9 acceptance 1: the submission ships the ten roster people.
MINIMUM_DOSSIERS = 10

#: The skip reason. It states WHY the check is not running, not merely that it is not —
#: an operator reading "skipped" with no cause concludes the corpus was checked.
HUMAN_GATE_REASON = (
    f"no committed dossiers at {DOSSIER_DIR.relative_to(REPO_ROOT)}/, so there is nothing "
    "to validate. Populating it is a HUMAN GATE outside an autonomous build's reach: it "
    "needs the live-network build of the ten real roster people (an Anthropic API key and "
    "a search account) followed by a fact-by-fact human review of every displayed fact at "
    "its source URL. These checks become real — and can fail — the moment that build is "
    "committed. They are NOT passing; nothing has been verified."
)


def committed_dossier_paths() -> list[Path]:
    """Every dossier file GIT ACTUALLY TRACKS, sorted. Empty until the corpus is committed.

    `git ls-files`, not `glob`, and the distinction is the whole point of this module.
    A glob answers "is it on disk"; the requirement (SPEC C4, S7) is that the app boots
    from COMMITTED dossiers, because `render.yaml` deploys from git and a file that was
    never added ships as an empty corpus.

    Measured 2026-09-04, which is why this changed: a live build wrote 10 dossiers and 59
    documents to disk and committed none of them. The glob found all 10, the skip did not
    fire, and this module reported a green "committed corpus" over a corpus git had never
    seen — reopening from the disk side exactly the hole the skip exists to close from the
    empty side. `subprocess` rather than a library: no new dependency, and `git` is already
    required to have produced this checkout.
    """
    if not DOSSIER_DIR.is_dir():
        return []
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "data/dossiers/*.json"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # No git (a source tarball, say). Fall back to disk rather than failing the suite,
        # and say so, because the guarantee is weaker on that path.
        return sorted(DOSSIER_DIR.glob("*.json"))
    tracked = sorted(REPO_ROOT / name for name in listed.split("\0") if name)
    return [path for path in tracked if path.is_file()]


@pytest.fixture(scope="module")
def dossier_paths() -> list[Path]:
    """The committed corpus, or a skip naming the human gate that populates it."""
    paths = committed_dossier_paths()
    if not paths:
        pytest.skip(HUMAN_GATE_REASON)
    return paths


def _load(path: Path) -> tuple[Dossier | None, str | None]:
    """``(dossier, None)`` or ``(None, problem)``.

    Validation failures are returned rather than raised so a malformed dossier is a test
    FAILURE naming the file, not a collection error that hides the other 11 files.
    """
    try:
        return Dossier.model_validate_json(path.read_text(encoding="utf-8")), None
    except ValidationError as exc:
        return None, f"{path.name} is not a valid Dossier: {exc}"
    except (OSError, ValueError) as exc:  # unreadable / not JSON at all
        return None, f"{path.name} could not be read as a Dossier: {exc!r}"


def test_committed_dossier_corpus_covers_the_roster(dossier_paths: list[Path]) -> None:
    """R16 / acceptance 1: at least the ten roster people are committed."""
    assert len(dossier_paths) >= MINIMUM_DOSSIERS, (
        f"R16 expects the ten roster people in {DOSSIER_DIR}; found {len(dossier_paths)}: "
        f"{[p.name for p in dossier_paths]}"
    )


def test_committed_dossiers_are_valid_against_the_contract(dossier_paths: list[Path]) -> None:
    """Every committed dossier parses as a ``Dossier`` and is internally consistent.

    Beyond schema validation this checks the identity and reference invariants that make
    the corpus loadable by the web app: the file is named for the person it holds, the
    resolution agrees with the person, ids are unique, and every hub cites a fact that is
    actually in the dossier (a dangling ``evidence_fact_ids`` entry renders a "why" with
    no evidence behind it).
    """
    problems: list[str] = []
    for path in dossier_paths:
        dossier, problem = _load(path)
        if dossier is None:
            problems.append(problem or f"{path.name} failed to load")
            continue

        person_id = dossier.person.person_id
        if person_id != path.stem:
            problems.append(
                f"{path.name} holds person_id {person_id!r}; DESIGN pins the filename to "
                f"data/dossiers/{{person_id}}.json, so the web app will not find it"
            )
        if dossier.resolution.person_id != person_id:
            problems.append(
                f"{path.name}: resolution.person_id {dossier.resolution.person_id!r} != "
                f"person.person_id {person_id!r}"
            )
        # `person_id = slug(name)`, plus an optional collision suffix (PersonRef).
        if not person_id.startswith(slug(dossier.person.name)):
            problems.append(
                f"{path.name}: person_id {person_id!r} is not slug({dossier.person.name!r}) "
                f"= {slug(dossier.person.name)!r} (nor that plus a collision suffix)"
            )

        fact_ids = [fact.fact_id for fact in dossier.facts]
        duplicates = sorted({fid for fid in fact_ids if fact_ids.count(fid) > 1})
        if duplicates:
            problems.append(f"{path.name}: duplicate fact_ids {duplicates}")
        known = set(fact_ids)
        for hub in dossier.hubs:
            dangling = [fid for fid in hub.evidence_fact_ids if fid not in known]
            if dangling:
                problems.append(
                    f"{path.name}: hub {hub.hub_id!r} cites fact_ids that are not in the "
                    f"dossier: {dangling}"
                )

    assert not problems, "committed dossiers are not valid:\n" + "\n".join(problems)


def test_every_displayable_fact_in_a_committed_dossier_quotes_its_rawdoc(
    dossier_paths: list[Path],
) -> None:
    """S6 / C8: every fact that may be SHOWN carries a quote really present in its source.

    C8 says an unquoted fact is dropped, not shown, so a displayed fact whose quote is not
    in the RawDoc it cites is the one defect this project cannot ship. Excluded, low
    confidence and never-displayable-kind facts are skipped on purpose: they never reach a
    screen (``taste.is_displayable``), so C8 says nothing about their quotes.

    The provenance is also checked AGAINST the document it names — url, source kind and
    the sha1-derived ``doc_id`` — because a quote checked against the wrong RawDoc is not
    a citation.
    """
    problems: list[str] = []
    checked = 0

    for path in dossier_paths:
        dossier, problem = _load(path)
        if dossier is None:
            problems.append(problem or f"{path.name} failed to load")
            continue

        for fact in dossier.facts:
            if not is_displayable(fact):
                continue
            provenance = fact.provenance
            doc_path = DOCS_DIR / f"{provenance.doc_id}.json"
            if not doc_path.is_file():
                problems.append(
                    f"{path.name}:{fact.fact_id} is displayed but cites a RawDoc that is "
                    f"not committed: {doc_path.relative_to(REPO_ROOT)}"
                )
                continue
            try:
                doc = RawDoc.model_validate_json(doc_path.read_text(encoding="utf-8"))
            except (ValidationError, OSError, ValueError) as exc:
                problems.append(f"{doc_path.name} is not a valid RawDoc: {exc!r}")
                continue

            checked += 1
            if doc.doc_id != provenance.doc_id:
                problems.append(
                    f"{doc_path.name} holds doc_id {doc.doc_id!r}, not {provenance.doc_id!r}"
                )
            if doc.url != provenance.url:
                problems.append(
                    f"{path.name}:{fact.fact_id} cites {provenance.url!r} but "
                    f"{doc_path.name} is {doc.url!r}"
                )
            if doc.source_kind != provenance.source_kind:
                problems.append(
                    f"{path.name}:{fact.fact_id} claims source_kind "
                    f"{provenance.source_kind!r}; {doc_path.name} is {doc.source_kind!r}"
                )
            if doc_id_for_url(provenance.url) != provenance.doc_id:
                problems.append(
                    f"{path.name}:{fact.fact_id}: doc_id {provenance.doc_id!r} is not "
                    f"sha1({provenance.url!r})[:16] = {doc_id_for_url(provenance.url)!r}"
                )
            if normalize_ws(provenance.quote) not in normalize_ws(doc.text):
                problems.append(
                    f"{path.name}:{fact.fact_id} is DISPLAYED, and its quote is not in "
                    f"{doc_path.name} (C8: an unquoted fact is dropped, not shown). "
                    f"quote={provenance.quote!r}"
                )

    assert not problems, (
        f"committed dossiers carry uncitable displayed facts "
        f"({len(problems)} problem(s) over {checked} displayed fact(s)):\n"
        + "\n".join(problems)
    )
    assert checked, (
        f"{len(dossier_paths)} dossier(s) are committed and not one carries a DISPLAYABLE "
        f"fact, so nothing was citation-checked. A corpus that shows nothing cannot be the "
        f"corpus the digest renders."
    )
