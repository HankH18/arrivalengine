"""R11 route: the taste layer rules on ``Fact.text``; the page renders
``Provenance.quote``; nothing rules on the quote.

``apply_taste_rules`` is ``[_decide(fact, rule_verdict(fact.text)) for fact in facts]`` —
``fact.text`` and nothing else. The LLM stage is handed the same sentence. So every R11
judgement in this product is made about the extractor's one-line paraphrase.

The digest page does not show only that paraphrase. ``digest.html`` renders
``row.fact.provenance.quote`` under every Lately bullet, ``non_obvious.provenance.quote``
under "Not on the first page", and ``entry.quote`` for every entry in "Why we know this".
A quote is up to 400 characters of somebody else's prose, lifted verbatim out of a fetched
document, and it is the ONE thing on the page that was never asked whether it is
R11 material.

**Reproduction.** A fact whose text is "Ann One published a guide to engineering
management." — professional, ``keep``, displayable — carrying the quote "Ann One's
psychiatrist told the podcast she was treated for depression in 2019." That sentence
renders TWICE on the host-facing digest, and ``rule_verdict`` returns
``exclude / health`` for it the moment it is actually asked. The filter is not missing the
category; it is never shown the string.

This is not currently exercised by ``data/dossiers/`` — no committed fact pairs a clean
text with an R11 quote — so this module reproduces the route rather than reporting a live
leak, and the passing test at the bottom is what will notice when the corpus grows one.

On the strict-xfail marker, see the module docstring of
``test_tadv_r11_hub_label_bypass.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arrival.taste import apply_taste_rules, is_displayable, rule_verdict
from arrival.web.app import create_app
from tadv_corpus import DeadLLM, committed_dossiers, synthetic_person, write_corpus

pytestmark = pytest.mark.ticket("TESTADVERSARY")

#: Professional on its face. No R11 cue anywhere in it.
CLEAN_TEXT = "Ann One published a guide to engineering management."

#: R11 `health`, and third-party disclosure at that — the rule layer's own worked example
#: of a deterministic exclude ("a colleague told a reporter..."). Written without an
#: apostrophe so a rendered-page assertion is not defeated by HTML entity escaping.
DIRTY_QUOTE = (
    "The psychiatrist treating Ann One told the podcast she was treated for depression "
    "in 2019."
)


@pytest.fixture
def digest_page(tmp_path):
    payload = synthetic_person(
        "ann-one",
        "Ann One",
        facts=[(CLEAN_TEXT, False, None)],
        hubs=[("company:northwind", "company", "Northwind", 0)],
    )
    # A Lately bullet: dated, recent, displayable. The quote rides along untouched.
    payload["facts"][0]["category"] = "recent_activity"
    payload["facts"][0]["provenance"]["quote"] = DIRTY_QUOTE
    payload["facts"][0]["provenance"]["published_at"] = "2026-08-20"
    write_corpus(tmp_path, [payload])

    client = TestClient(create_app(dossier_dir=tmp_path, llm=DeadLLM()))
    response = client.post("/arrive", json={"person_id": "ann-one"})
    assert response.status_code == 200
    page = client.get(response.json()["digest_url"])
    assert page.status_code == 200
    return page.text


# --------------------------------------------------------------------------- premises

def test_the_filter_would_have_caught_the_quote_if_it_had_been_asked():
    """The category is in the vocabulary. The string is simply never submitted to it."""
    assert rule_verdict(CLEAN_TEXT).decision == "keep"
    verdict = rule_verdict(DIRTY_QUOTE)
    assert verdict.decision == "exclude"
    assert verdict.reason == "health"


def test_the_taste_layer_reads_only_the_fact_text(tmp_path):
    """`apply_taste_rules` keeps the fact, because it never looks at the provenance."""
    from arrival.contracts import Dossier

    payload = synthetic_person(
        "ann-one",
        "Ann One",
        facts=[(CLEAN_TEXT, False, None)],
        hubs=[("company:northwind", "company", "Northwind", 0)],
    )
    payload["facts"][0]["provenance"]["quote"] = DIRTY_QUOTE
    dossier = Dossier.model_validate(payload)
    (decided,) = apply_taste_rules(dossier.facts)
    assert decided.excluded is False
    assert is_displayable(decided) is True


def test_the_clean_half_really_is_shown(digest_page):
    """Positive control: the fixture reaches the page at all."""
    assert CLEAN_TEXT in digest_page


# --------------------------------------------------------------------------- the leak

def test_an_r11_quote_never_reaches_the_digest(digest_page):
    """R11 binds to what a host SEES, not to the sentence the extractor wrote."""
    assert DIRTY_QUOTE not in digest_page


# ------------------------------------------------------- the live corpus, for the future

def test_no_committed_fact_pairs_a_clean_text_with_an_r11_quote():
    """Passes today. It is the tripwire for the corpus growing into the route above."""
    offenders = []
    for dossier in committed_dossiers():
        for fact in dossier.facts:
            if not is_displayable(fact):
                continue
            if rule_verdict(fact.text).decision != "keep":
                continue
            quote_verdict = rule_verdict(fact.provenance.quote)
            if quote_verdict.decision == "exclude":
                offenders.append(
                    f"{dossier.person.person_id} {fact.fact_id} "
                    f"[{quote_verdict.reason}]: {fact.provenance.quote}"
                )
    assert offenders == [], "\n" + "\n".join(offenders)
