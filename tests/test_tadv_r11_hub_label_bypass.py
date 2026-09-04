"""R11 route: a taste-EXCLUDED fact's hub LABEL is published on every host-facing page.

**Reproduction, and it is four surfaces rather than one.** Two members each carry a fact
the taste filter excluded with reason ``home_or_property`` — the sentence naming the
street they live on. Both facts are correctly suppressed: the sentence appears nowhere.
The ``Hub`` those facts back carries the same street name as its ``label``, and that label
is rendered verbatim:

1. in the Meet row's ``why`` — **"Both rooted in Ravensworth Hill."** — which R18 says a
   host reads OUT LOUD, in a lobby, to the member it is about;
2. in the R10 ``data-reasoning`` score-components table on the same digest page;
3. in ``/graph``'s shared-hub sentence ("Shared: … Ravensworth Hill (Ann One and Bob
   Two)"), linked from the nav of every page;
4. in ``/corpus``'s "What the corpus records <name> as connected to" table.

**Why it happens.** ``graph.py`` deliberately does not filter hubs, on the stated ground
that matching is not display — and that ground is sound for MATCHING. But ``graph._why``
turns a hub label into a sentence, and three templates print ``hub.label`` directly. So
``taste.is_displayable`` gates the fact text and nothing at all gates the label, even when
the label's ONLY evidence is a fact the filter already ruled an R11 violation and
annotated with the category it violates.

**What SHOULD be true**, and what the xfailing tests below assert: a hub whose evidence is
entirely taste-excluded must not have its label rendered on a host-facing page. That is
strictly weaker than "filter hubs" — a hub with even one displayable evidence fact is
untouched, so the matching design is unaffected. ``/debug`` is exempt: R15 makes it the
one page permitted to show withheld material.

**On the xfail markers.** These are ``strict=True`` and they are NOT an assertion being
relaxed — nothing here existed before, and no existing assertion is touched. This lane is
read-only on ``src/``; a defect it finds is a report. A strict xfail is the honest
encoding of "this assertion states the requirement, the requirement is violated today,
and the day someone fixes it this file goes RED so the marker must be deleted rather than
the defect forgotten." The passing tests in this module lock in the half that already
works — the excluded fact's TEXT really is suppressed everywhere.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from arrival.taste import R11_CATEGORIES
from arrival.web.app import create_app
from tadv_corpus import DeadLLM, synthetic_person, write_corpus

pytestmark = pytest.mark.ticket("TESTADVERSARY")

#: The secret. A street a member lives on: R11's ``home_or_property`` category, and a
#: string that appears in the corpus for exactly one reason, so finding it in a rendered
#: page is unambiguous.
SECRET = "Ravensworth Hill"

#: Enough people that the shared hub's IDF does not clamp to zero. ``idf = max(0, ln(N /
#: (1 + n)))``, so with N=4 carriers=2 the hub is worth ``ln(4/3) > 0`` and ``graph._why``
#: — which names only hubs with ``contribution > 0`` — will actually speak its label.
FILLER = [("cid-three", "Cid Three"), ("dot-four", "Dot Four")]


@pytest.fixture
def leaky_app(tmp_path):
    """Four members; two share a hub whose only evidence is an excluded R11 fact."""
    payloads = []
    for person_id, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]:
        payloads.append(
            synthetic_person(
                person_id,
                name,
                facts=[(f"{name}'s home is on {SECRET}.", True, "home_or_property")],
                hubs=[("city:ravensworth-hill", "city", SECRET, 0)],
            )
        )
    for person_id, name in FILLER:
        payloads.append(
            synthetic_person(
                person_id,
                name,
                facts=[(f"{name} works at Northwind.", False, None)],
                hubs=[("company:northwind", "company", "Northwind", 0)],
            )
        )
    write_corpus(tmp_path, payloads)
    return create_app(dossier_dir=tmp_path, llm=DeadLLM())


@pytest.fixture
def leaky_digest(leaky_app):
    """The page a host reads when Bob Two arrives with Ann One already in the building."""
    client = TestClient(leaky_app)
    for person_id in ["ann-one", "cid-three", "dot-four"]:
        assert client.post("/arrive", json={"person_id": person_id}).status_code == 200
    response = client.post("/arrive", json={"person_id": "bob-two"})
    assert response.status_code == 200
    page = client.get(response.json()["digest_url"])
    assert page.status_code == 200
    return client, page.text


# --------------------------------------------------------------------------- what works

def test_the_excluded_sentence_itself_is_suppressed_everywhere(leaky_digest):
    """The half of R11 that holds: an excluded FACT's text reaches no host-facing page."""
    client, digest = leaky_digest
    sentence = f"Ann One's home is on {SECRET}."
    assert sentence not in digest
    assert sentence not in client.get("/corpus").text
    assert sentence not in client.get("/graph").text
    assert sentence not in client.get("/").text


def test_the_fixture_really_did_exclude_the_fact(leaky_app):
    """A positive control. Without this, every assertion below could pass vacuously."""
    dossier = leaky_app.state.store.get("ann-one")
    assert dossier is not None
    (fact,) = dossier.facts
    assert fact.excluded is True
    assert fact.exclusion_reason == "home_or_property"
    assert fact.exclusion_reason in R11_CATEGORIES
    # ...and the hub really does carry the secret, backed by nothing else.
    (hub,) = dossier.hubs
    assert hub.label == SECRET
    assert hub.evidence_fact_ids == [fact.fact_id]


# --------------------------------------------------------------------------- what leaks

def test_the_spoken_why_line_never_names_an_excluded_hub(leaky_digest):
    """R11 + R18: the sentence a host says out loud must not carry withheld material."""
    _client, digest = leaky_digest
    spoken = re.findall(r'class="why">([^<]*)', digest)
    assert spoken, "expected at least one Meet row"
    assert not any(SECRET in line for line in spoken), spoken


def test_the_digest_page_never_prints_an_excluded_hub_label(leaky_digest):
    """R11: the digest is host-facing top to bottom, disclosure blocks included."""
    _client, digest = leaky_digest
    assert SECRET not in digest


def test_the_graph_page_never_prints_an_excluded_hub_label(leaky_digest):
    """R17's page is host-facing: /debug is the only view permitted withheld material."""
    client, _digest = leaky_digest
    assert SECRET not in client.get("/graph").text


def test_the_corpus_page_never_prints_an_excluded_hub_label(leaky_digest):
    """/corpus is the demo page and states its own R11 footer; it is host-facing."""
    client, _digest = leaky_digest
    assert SECRET not in client.get("/corpus").text
