"""FROZEN ACCEPTANCE - ticket T-9: the ship artifacts the swarm can actually build.

Graded requirements: R16, S6, S7 (artifact half), S8 (artifact half), C4, C8,
SPEC open questions Q1-Q5.

READ THIS BEFORE ADDING TESTS HERE. T-9's live-network build of the ten real people,
the fact-by-fact hand review of every dossier, and the Render deploy are HUMAN GATES.
The swarm has no API keys, no search account and no human reviewer, so it cannot do
them, and a frozen test that pretended otherwise would either be permanently red for a
reason no worker can fix, or - far worse - green against nothing. So these tests grade
only the CODE ARTIFACTS: `render.yaml`, the README, and the committed-dossier
validator. The validator's own behaviour on an empty `data/dossiers/` is graded
directly, because "a validator that passes on an empty directory" is exactly the
defect that would let the human gate be skipped silently.

That last test asserts a property of a TICKET-OWNED test file
(`tests/test_t9_committed_dossiers.py`). That is deliberate and is not the banned
"test about the frozen suite": the T-9 deliverable IS that validator, so its
skip-rather-than-pass behaviour is the acceptance criterion, not harness trivia.
"""

from __future__ import annotations

import ast
import re
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

# A skip reason that names the human gate. Generous on wording, strict on substance:
# it must say WHY the check is not running, not merely that it is not running.
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
    directory = repo_root / "data" / "dossiers"
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


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
    assert re.search(r"cold[- ]?start|spins? down|spin[- ]?down|goes to sleep|sleeps|warm[- ]?up|wake", readme), (
        "the README does not warn about the Render free tier's cold start; a demo that "
        "opens on a sleeping instance looks broken"
    )


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


def test_committed_dossier_validator_refuses_to_pass_on_an_empty_data_dossiers(
    repo_root, tmp_path
):
    """T-9 acceptance 1: the ticket's validator SKIPS with a stated human gate, never passes empty."""
    path = repo_root / VALIDATOR_PATH
    source = _read(path)

    assert "skip" in source, (
        f"{VALIDATOR_PATH} contains no skip at all; with no committed dossiers it can "
        "only pass vacuously or fail for the wrong reason"
    )
    literals = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    reasons = [s for s in literals if "dossier" in s.lower() and GATE_WORDS.search(s)]
    assert reasons, (
        f"no string in {VALIDATOR_PATH} explains that the empty data/dossiers/ is a "
        f"human gate. Strings mentioning dossiers: "
        f"{[s for s in literals if 'dossier' in s.lower()][:6]}"
    )

    if _committed_dossiers(repo_root):
        # The human gate has run: the skip path is no longer reachable, and the static
        # guard above is all this test can honestly assert.
        return

    report = tmp_path / "validator.xml"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(path),
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
            f"--junit-xml={report}",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert report.is_file(), (
        f"running {VALIDATOR_PATH} produced no junit report (exit {proc.returncode}).\n"
        f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    )
    cases = list(ET.parse(report).getroot().iter("testcase"))
    assert cases, f"{VALIDATOR_PATH} collected no tests at all"

    dossier_cases = [c for c in cases if "dossier" in (c.get("name") or "").lower()] or cases
    vacuous = [
        c.get("name")
        for c in dossier_cases
        if c.find("skipped") is None and c.find("failure") is None and c.find("error") is None
    ]
    assert not vacuous, (
        f"data/dossiers/ is empty, yet {VALIDATOR_PATH} reports {vacuous} as PASSING. "
        "A validator that is green on an empty directory lets the human gate be skipped."
    )
    messages = [
        (c.find("skipped").get("message") or "") + (c.find("skipped").text or "")
        for c in dossier_cases
        if c.find("skipped") is not None
    ]
    assert messages, f"{VALIDATOR_PATH} neither passed nor skipped; it failed instead"
    assert any(GATE_WORDS.search(m) for m in messages), (
        f"the skip reason does not name the human gate: {messages}"
    )


def test_committed_dossiers_validate_and_every_displayable_quote_is_in_its_rawdoc(repo_root):
    """T-9 acceptance 1 / S6 / C8: every committed dossier validates and every shown quote is real."""
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

    docs_dir = repo_root / "data" / "docs"
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
    assert not problems, "committed dossiers carry uncitable displayed facts:\n" + "\n".join(problems)
