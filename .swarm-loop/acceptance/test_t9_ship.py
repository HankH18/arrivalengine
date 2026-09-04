"""FROZEN ACCEPTANCE - ticket T-9: the ship artifacts the swarm can actually build.

Graded requirements: R16, S6, S7 (artifact half), S8 (artifact half), C4, C8,
SPEC open questions Q1-Q5.

READ THIS BEFORE ADDING TESTS HERE. T-9's live-network build of the ten real people,
the fact-by-fact hand review of every dossier, and the Render deploy are HUMAN GATES.
The swarm has no API keys, no search account and no human reviewer, so it cannot do
them, and a frozen test that pretended otherwise would either be permanently red for a
reason no worker can fix, or - far worse - green against nothing. So these tests grade
only the CODE ARTIFACTS: `render.yaml`, the README, and the committed-dossier
validator. The one criterion that genuinely needs the human is marked
`@pytest.mark.human_gate`: it stays collected and stays reported, and run.py excludes
it from `acceptance_pass_rate` alone, so a goal nobody CAN reach is never mistaken for
a goal nobody HIT. (Skips stay in that denominator by design; leaving a permanent skip
in the scored set put the 100 target out of reach by construction.)

HOW THE VALIDATOR IS GRADED, and why it is not graded the way it used to be.
`tests/test_t9_committed_dossiers.py` is inside T-9's own write scope. Grading it by
reading its SOURCE - "does the file contain the word skip", "does some string literal
match a regex" - measured nothing at all: a four-line module containing one
unconditional `pytest.skip("... human gate ...")` satisfied every such check while
validating no dossier. Worse, the old version short-circuited once `data/dossiers/`
was populated, so after the human build the vacuous file was never examined again.

So the validator is now graded on BEHAVIOUR, against corpora IT DOES NOT CONTROL. Each
test builds a throwaway repo skeleton in `tmp_path`, fills `data/dossiers/` and
`data/docs/` from the ORCHESTRATOR-OWNED frozen fixture corpus, copies the validator
into it, and runs it there. Three corpora, three required outcomes:

  * empty `data/dossiers/`  -> must SKIP, naming the human gate; must never pass.
  * a corpus of >= 10 valid dossiers whose every quote really is in its RawDoc
                            -> must PASS.
  * the SAME corpus with the displayed quotes replaced by a fabricated sentence
                            -> must FAIL.

The two halves of that pair differ in nothing but the quote strings, so "passes one,
fails the other" is by construction a statement about quote checking and about nothing
else. No unconditional-skip file can satisfy it, because a skip is not a failure.

Every subprocess here uses the SAME isolation flags run.py's `_pytest_argv` uses -
`-o addopts=`, `-c <frozen pytest.ini>`, `--confcutdir`, `--rootdir`,
`-p no:cacheprovider` - and runs inside the throwaway skeleton rather than the real
tree. Measured on this repo: `--rootdir` alone does NOT stop pytest's configfile
discovery, and a two-line worker-writable `pyproject.toml` took the frozen suite from
93 collected to 12 without touching one frozen byte. `-c` is the flag that overrides
discovery outright, and this module's invocations were the only ones in the harness
that lacked it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

# Two markers, deliberately. `t9` is the marker NAME this suite's own
# conftest mandates for selection (`pytest -m t9`), and every scored metric
# selects on it. `ticket("T-9")` is the ARGUMENT form the freeze-time coverage
# and read-edge gates parse out of the AST; without it those gates see a suite
# with zero attributed tests and freeze refuses every ticket. Additive on
# purpose: neither dialect replaces the other.
pytestmark = [pytest.mark.t9, pytest.mark.ticket("T-9")]


VALIDATOR_PATH = Path("tests") / "test_t9_committed_dossiers.py"

# The frozen pytest config, passed as `-c` to every validator subprocess. Absolute and
# inside the frozen manifest, so no worker-writable ini can displace it.
FROZEN_INI = Path(__file__).resolve().parent / "pytest.ini"

# T-9 acceptance 1 and DESIGN "Dossier file": the committed corpus lives at
# `data/dossiers/{person_id}.json` and the RawDoc each fact cites at
# `data/docs/{doc_id}.json`. The synthetic corpora below are laid out that way; a
# validator that reads some other layout will not find them, and the assertion
# messages say so rather than leaving a mystery red.
DOSSIER_DIR = Path("data") / "dossiers"
DOCS_DIR = Path("data") / "docs"

# R16 / T-9 acceptance 1: ">= 10 files in data/dossiers/". The good corpus clears that
# bar so a validator enforcing it is not failed by the fixture instead of by the code.
GOOD_CORPUS_SIZE = 12

# Substituted for every displayed fact's quote in the corrupted corpus. Prose, so it
# violates no field constraint, and present in no RawDoc in the frozen corpus.
FABRICATED_QUOTE = (
    "This sentence was fabricated by the frozen acceptance harness and appears in no "
    "committed RawDoc."
)

# A skip reason that names the human gate. Generous on wording, strict on substance:
# it must say WHY the check is not running, not merely that it is not running. Matched
# against the RUNTIME skip message in the junit report - an observable behaviour of the
# validator - never against its source text.
GATE_WORDS = re.compile(
    r"human|manual|hand[- ]?review|gate|owner|not (yet )?(been )?run|has not run|"
    r"live (network|build)|real (roster|people)|api key",
    re.IGNORECASE,
)

# ASGI target inside a Render start command, e.g. "arrival.web.app:app".
ASGI_TARGET_RE = re.compile(r"([A-Za-z_][\w.]*):([A-Za-z_]\w*)")

MARKDOWN_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def _read(path):
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _headings(markdown):
    return [m.group(1).strip().lower() for m in MARKDOWN_HEADING_RE.finditer(markdown)]


def _committed_dossiers(repo_root):
    directory = repo_root / DOSSIER_DIR
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


# ---------------------------------------------------------------------------
# Behavioural grading of the committed-dossier validator.
# ---------------------------------------------------------------------------


def _hermetic_env(repo_root):
    """The env run.py uses, for the same two reasons it uses it.

    PYTHONPATH is pinned rather than inherited so an inert editable install cannot make
    the validator look broken, and PYTHONDONTWRITEBYTECODE keeps a subprocess from
    writing .pyc files into the frozen, hash-protected acceptance directory.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _build_corpus(root, frozen_fixtures, n_dossiers, corrupt_quotes):
    """Lay out a throwaway repo skeleton at `root` holding a committed-dossier corpus.

    Dossiers are cloned from the frozen fixture corpus - orchestrator-owned, inside no
    ticket's scope - and re-keyed so each clone is a distinct person with distinct fact
    ids. Only the identifiers move; doc_ids, urls and (unless `corrupt_quotes`) quotes
    are left exactly as frozen, so the corpus's quote-in-RawDoc property is inherited
    rather than re-invented here.
    """
    source_docs = frozen_fixtures / "docs"
    source_dossiers = frozen_fixtures / "dossiers"
    bases = sorted(source_dossiers.glob("*.json"))
    assert bases, f"the frozen fixture corpus has no dossiers: {source_dossiers}"

    docs = root / DOCS_DIR
    dossiers = root / DOSSIER_DIR
    docs.mkdir(parents=True, exist_ok=True)
    dossiers.mkdir(parents=True, exist_ok=True)
    # A .git marker and a project file so a validator that locates the repo root by
    # walking upwards stops HERE and not in the real tree, where the real (empty)
    # data/dossiers/ would silently replace the corpus under test.
    (root / ".git").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "arrival-acceptance-fixture"\nversion = "0"\n', encoding="utf-8"
    )

    for source in sorted(source_docs.glob("*.json")):
        shutil.copyfile(source, docs / source.name)

    for index in range(n_dossiers):
        base = bases[index % len(bases)]
        dossier = json.loads(base.read_text(encoding="utf-8"))
        old_id = dossier["person"]["person_id"]
        new_id = f"{old_id}-{index:02d}"
        dossier["person"]["person_id"] = new_id
        dossier["person"]["name"] = f"{dossier['person']['name']} {index:02d}"
        dossier["resolution"]["person_id"] = new_id
        renamed = {}
        for fact in dossier["facts"]:
            new_fact_id = fact["fact_id"].replace(old_id, new_id, 1)
            renamed[fact["fact_id"]] = new_fact_id
            fact["fact_id"] = new_fact_id
            # Only NON-excluded facts are corrupted: an excluded fact is never shown, so
            # C8 says nothing about its quote and a validator is right to ignore it.
            if corrupt_quotes and not fact.get("excluded"):
                fact["provenance"]["quote"] = FABRICATED_QUOTE
        for hub in dossier.get("hubs", []):
            hub["evidence_fact_ids"] = [
                renamed.get(fact_id, fact_id) for fact_id in hub.get("evidence_fact_ids", [])
            ]
        (dossiers / f"{new_id}.json").write_text(
            json.dumps(dossier, indent=2), encoding="utf-8"
        )
    return root


def _install_validator(root, repo_root):
    """Copy the ticket's validator, with the helpers it lives among, into the skeleton.

    The whole `tests/` tree comes along so the validator finds the conftest and helper
    modules it normally imports, then every OTHER test module is removed: this run is
    about the validator, and another ticket's red test showing up in its junit report
    would be attributed to T-9.
    """
    validator = repo_root / VALIDATOR_PATH
    assert validator.is_file(), (
        f"{VALIDATOR_PATH} is missing - T-9 acceptance 1 asks for a validator that "
        f"checks the committed dossiers, and there is nothing here to run"
    )
    destination = root / VALIDATOR_PATH.parent
    shutil.copytree(
        repo_root / VALIDATOR_PATH.parent,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    for path in sorted(destination.rglob("test_*.py")):
        if path.name != validator.name:
            path.unlink()
    return destination / validator.name


def _run_validator(root, target, junit, repo_root):
    """Run the validator inside the skeleton, with run.py's isolation flags.

    Mirrors `run.py._pytest_argv`. `-c` is the load-bearing one: measured on this repo,
    `--rootdir` alone does not stop pytest reading a worker-writable `pyproject.toml`,
    and this module's invocation used to inherit both that file and the project's root
    conftest.
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        str(target),
        "-q",
        "-rs",
        "--tb=short",
        "-o",
        "addopts=",
        "-c",
        str(FROZEN_INI),
        "-p",
        "no:cacheprovider",
        "--confcutdir",
        str(target.parent),
        "--rootdir",
        str(root),
        f"--junit-xml={junit}",
    ]
    proc = subprocess.run(
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        env=_hermetic_env(repo_root),
    )
    report = (
        f"$ {' '.join(argv)}\n\nexit={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-4000:]}"
    )
    assert junit.is_file(), f"running {VALIDATOR_PATH} produced no junit report.\n{report}"
    return report


def _outcomes(junit):
    """[(name, status, message)] for every testcase in a junit report."""
    results = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        for status in ("error", "failure", "skipped"):
            node = case.find(status)
            if node is not None:
                message = f"{node.get('message') or ''} {node.text or ''}".strip()
                results.append((case.get("name") or "?", status, message))
                break
        else:
            results.append((case.get("name") or "?", "passed", ""))
    return results


def _dossier_cases(outcomes):
    """The validator's dossier tests, or all of them when none names a dossier."""
    return [row for row in outcomes if "dossier" in row[0].lower()] or outcomes


def _grade(tmp_path, repo_root, frozen_fixtures, label, n_dossiers, corrupt_quotes):
    """Build a corpus, run the validator against it, and return (outcomes, report)."""
    root = _build_corpus(
        tmp_path / label, frozen_fixtures, n_dossiers=n_dossiers, corrupt_quotes=corrupt_quotes
    )
    target = _install_validator(root, repo_root)
    junit = tmp_path / f"{label}.xml"
    report = _run_validator(root, target, junit, repo_root)
    outcomes = _outcomes(junit)
    assert outcomes, f"{VALIDATOR_PATH} collected no tests at all.\n{report}"
    return outcomes, report


def test_render_yaml_declares_a_web_service_whose_start_command_boots_the_app(repo_root):
    """C4 / S7: render.yaml declares a Render web service whose start command boots the ASGI app."""
    import importlib

    import yaml

    document = yaml.safe_load(_read(repo_root / "render.yaml"))
    assert isinstance(document, dict), f"render.yaml is not a mapping: {type(document).__name__}"
    services = document.get("services")
    assert services, f"render.yaml declares no services: {sorted(document)}"

    web = [
        service
        for service in services
        if isinstance(service, dict) and str(service.get("type", "")).lower() == "web"
    ]
    assert web, f"render.yaml declares no service of type 'web': {services!r}"
    service = web[0]

    start = ""
    for key in ("startCommand", "start_command", "startcommand", "dockerCommand"):
        if service.get(key):
            start = str(service[key])
            break
    assert start.strip(), f"the web service has no start command: {sorted(service)}"
    assert "PORT" in start, (
        f"the start command does not bind Render's $PORT, so the service never goes "
        f"live: {start!r}"
    )

    targets = ASGI_TARGET_RE.findall(start)
    target = next((t for t in targets if t[0].split(".")[0] == "arrival"), None)
    assert target, (
        f"the start command names no ASGI target of the form arrival.…:app - it cannot "
        f"boot this app: {start!r}"
    )
    module_name, attribute = target

    # C4: the deployed instance must be constructible with no build-time network access
    # and, at this point in the run, with no committed dossiers either.
    module = importlib.import_module(module_name)
    asgi_app = getattr(module, attribute, None)
    assert asgi_app is not None, (
        f"the start command points at {module_name}:{attribute}, which does not exist"
    )
    assert callable(asgi_app) or hasattr(asgi_app, "routes"), (
        f"{module_name}:{attribute} is not an ASGI application: {type(asgi_app).__name__}"
    )


def test_readme_documents_the_free_tier_cold_start_warm_up(repo_root):
    """C4 / S7: the README warns the demo operator that the free tier sleeps and must be warmed."""
    readme = _read(repo_root / "README.md").lower()
    assert "render" in readme, "the README never mentions the host the app is deployed to"
    assert re.search(
        r"cold[- ]?start|spins? down|spin[- ]?down|goes to sleep|sleeps|warm[- ]?up|wake", readme
    ), (
        "the README does not warn about the Render free tier's cold start; a demo that "
        "opens on a sleeping instance looks broken"
    )


# GUARD, not a scored T-9 criterion. T-0 deliberately shipped the README skeleton with
# every required heading, so this is green at baseline BY DESIGN -- which is exactly
# what `guard` means. Unmarked it fed a free point into any criteria_t9 metric and was
# the single passing test in the scored set. It still grades something real: T-9 must
# not REMOVE the structure T-0 established. The section CONTENT is graded by the
# sibling tests, which are red until T-9 writes it.
@pytest.mark.guard
def test_readme_contains_the_required_sections(repo_root):
    """R16 / S8: the README carries every section the submission is graded on."""
    text = _read(repo_root / "README.md")
    headings = _headings(text)
    assert headings, "README.md has no markdown headings at all"

    required = {
        "how to run": r"\brun\b|quickstart|getting started|\binstall",
        "exclusion policy": r"exclusion|taste|never (show|surface|display)",
        "hours log": r"hours",
        "deploy": r"deploy|live url|public url",
        "what I would build next": r"\bnext\b",
    }
    missing = [
        label
        for label, pattern in required.items()
        if not any(re.search(pattern, heading) for heading in headings)
    ]
    assert not missing, f"README has no heading for: {missing}. Headings found: {headings}"

    assert re.search(r"(?im)^.*deploy(ed|ment)?[^\n]{0,40}url[^\n]*$", text), (
        "R16 asks for the deploy URL; the README has a deploy section but no line "
        "naming the URL (a placeholder line is fine until the human gate runs)"
    )


def test_readme_answers_the_spec_open_questions_q1_to_q5(repo_root):
    """SPEC 'Open questions' / T-9 acceptance 3: Q1-Q5 are answered in the README."""
    text = _read(repo_root / "README.md")
    unanswered = [q for q in ("Q1", "Q2", "Q3", "Q4", "Q5") if not re.search(rf"\b{q}\b", text)]
    assert not unanswered, f"the README never addresses: {unanswered}"
    assert re.search(r"(?im)^[ \t]{0,3}#{1,6}[ \t]+.*(open question|assumption|\bq1\b)", text), (
        "the Q1-Q5 answers are not under a section a reader can find"
    )


def test_committed_dossier_validator_skips_rather_than_passes_on_an_empty_data_dossiers(
    repo_root, frozen_fixtures, tmp_path
):
    """T-9 acceptance 1: on an empty corpus the validator SKIPS with a stated human gate.

    Run against a SYNTHETIC empty `data/dossiers/`, not the real one, so this stays a
    live check after the human build lands. The old version returned early once the real
    directory was populated, which retired the check exactly when the file it grades had
    stopped being watched.
    """
    outcomes, report = _grade(
        tmp_path, repo_root, frozen_fixtures, "empty", n_dossiers=0, corrupt_quotes=False
    )
    cases = _dossier_cases(outcomes)

    errors = [(name, message) for name, status, message in cases if status == "error"]
    assert not errors, (
        f"{VALIDATOR_PATH} could not even be collected against an empty "
        f"data/dossiers/: {errors}\n{report}"
    )
    vacuous = [name for name, status, _ in cases if status == "passed"]
    assert not vacuous, (
        f"data/dossiers/ is empty, yet {VALIDATOR_PATH} reports {vacuous} as PASSING. "
        f"A validator that is green on an empty directory lets the human gate be "
        f"skipped in silence.\n{report}"
    )
    skips = [message for _, status, message in cases if status == "skipped"]
    assert skips, (
        f"{VALIDATOR_PATH} neither passed nor skipped on an empty data/dossiers/; it "
        f"failed instead, which is red for a reason no worker can fix.\n{report}"
    )
    assert any(GATE_WORDS.search(message) for message in skips), (
        f"the skip reason does not say WHY the check is not running - it must name the "
        f"human gate, not merely announce a skip: {skips}\n{report}"
    )


def test_committed_dossier_validator_passes_a_corpus_whose_quotes_are_all_real(
    repo_root, frozen_fixtures, tmp_path
):
    """T-9 acceptance 1: the validator accepts a committed corpus that is actually sound.

    The other half of the discrimination pair below. Without it, a validator that simply
    failed on everything would look like a quote checker.
    """
    outcomes, report = _grade(
        tmp_path,
        repo_root,
        frozen_fixtures,
        "sound",
        n_dossiers=GOOD_CORPUS_SIZE,
        corrupt_quotes=False,
    )
    cases = _dossier_cases(outcomes)

    bad = [
        (name, status, message)
        for name, status, message in cases
        if status in ("failure", "error")
    ]
    assert not bad, (
        f"{VALIDATOR_PATH} rejects a corpus of {GOOD_CORPUS_SIZE} valid dossiers whose "
        f"every quote IS present in the RawDoc at data/docs/<doc_id>.json. The corpus "
        f"is the frozen fixture corpus, re-keyed. The skeleton it runs in holds ONLY "
        f"data/dossiers/*.json, data/docs/*.json and a copy of tests/ - that is the "
        f"layout T-9 acceptance 1 names, and a validator that additionally requires a "
        f"committed input this corpus cannot supply (a roster, a cache, a built "
        f"digest) must degrade rather than fail on it: {bad}\n{report}"
    )
    passed = [name for name, status, _ in cases if status == "passed"]
    assert passed, (
        f"{VALIDATOR_PATH} did not PASS on a sound corpus of {GOOD_CORPUS_SIZE} "
        f"dossiers - every case skipped. A validator that skips whatever it is given "
        f"grades nothing: {cases}\n{report}"
    )


def test_committed_dossier_validator_fails_when_a_displayed_quote_is_not_in_its_rawdoc(
    repo_root, frozen_fixtures, tmp_path
):
    """S6 / C8: the validator catches a displayed fact whose quote is not in its RawDoc.

    Identical to the sound corpus above in every byte except the quote strings, so
    "passes that one, fails this one" is a statement about quote checking and nothing
    else. An unconditional skip cannot satisfy it: a skip is not a failure.
    """
    outcomes, report = _grade(
        tmp_path,
        repo_root,
        frozen_fixtures,
        "fabricated",
        n_dossiers=GOOD_CORPUS_SIZE,
        corrupt_quotes=True,
    )
    cases = _dossier_cases(outcomes)

    failures = [name for name, status, _ in cases if status == "failure"]
    assert failures, (
        f"every displayed fact in all {GOOD_CORPUS_SIZE} committed dossiers carries the "
        f"quote {FABRICATED_QUOTE!r}, which appears in no RawDoc - and "
        f"{VALIDATOR_PATH} did not fail. C8 says an unquoted fact is dropped, not "
        f"shown, so this corpus must be rejected. Outcomes: {cases}\n{report}"
    )


@pytest.mark.human_gate
def test_committed_dossiers_validate_and_every_displayable_quote_is_in_its_rawdoc(repo_root):
    """T-9 acceptance 1 / S6 / C8: every committed dossier validates and every shown quote is real.

    HUMAN GATE. `data/dossiers/` is populated only by the live-network build of the ten
    real people followed by a fact-by-fact hand review - no API key, no search account
    and no reviewer exists inside the swarm, so this can only ever SKIP here. run.py
    keeps skips in the `acceptance_pass_rate` denominator on purpose, so leaving this in
    the scored set made 100 unreachable by construction; `human_gate` takes it out of
    that one rate while keeping it collected, selected by `-m t9`, and reported. It
    becomes a real, failing-capable check the moment the human build is committed - and
    the three tests above already grade the validator's behaviour today, so nothing
    about the CODE artifact waits on this.
    """
    files = _committed_dossiers(repo_root)
    if not files:
        pytest.skip(
            "data/dossiers/ is empty: building the ten real people needs live network "
            "access and a fact-by-fact human review, which are HUMAN GATES outside the "
            "swarm's reach. This check is real the moment that build is committed."
        )

    from arrival.contracts import Dossier, RawDoc
    from arrival.taste import is_displayable
    from arrival.util import normalize_ws

    assert len(files) >= 10, f"R16 expects the ten roster people; found {len(files)} dossier(s)"

    docs_dir = repo_root / DOCS_DIR
    problems = []
    for path in files:
        dossier = Dossier.model_validate_json(path.read_text(encoding="utf-8"))
        for fact in dossier.facts:
            if not is_displayable(fact):
                continue
            doc_path = docs_dir / f"{fact.provenance.doc_id}.json"
            if not doc_path.is_file():
                problems.append(f"{path.name}:{fact.fact_id} cites missing {doc_path}")
                continue
            doc = RawDoc.model_validate_json(doc_path.read_text(encoding="utf-8"))
            if normalize_ws(fact.provenance.quote) not in normalize_ws(doc.text):
                problems.append(
                    f"{path.name}:{fact.fact_id} quote is not in {doc_path.name} "
                    f"(C8: an unquoted fact is dropped, not shown)"
                )
    assert not problems, "committed dossiers carry uncitable displayed facts:\n" + "\n".join(
        problems
    )
