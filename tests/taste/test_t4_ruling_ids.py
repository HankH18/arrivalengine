"""T-4 — a ruling only counts for a fact we actually asked that call about.

``apply_taste`` batches its unsure facts and asks the classifier to answer "keyed by id".
The id in the answer is a string the MODEL chose. Every sibling stage in this codebase
already refuses to treat such a string as an identity — ``resolve._verdict_from`` says so
in as many words ("the doc_id is OURS, never the model's echo of it") and
``extract._collect_facts`` routes every claimed id through an ``id_map`` and drops the
references that do not resolve. Taste is the stage where a mistake is most expensive: a
verdict landing on the wrong fact does not lose a fact, it PUBLISHES one — the R11
sentence stays in the digest wearing the innocent fact's ``keep``.

The three ways a model-chosen id can be wrong, each graded here:

1. **Across batches.** The rulings dict outlives one call, so a batch-2 answer naming a
   batch-1 fact overwrites a ruling that was already made and already correct. This is the
   reproduction: batch 1 excludes a health sentence, batch 2 echoes its id with ``keep``,
   and the health sentence is displayed.
2. **Within one answer.** Two rulings sharing an id is not an answer, it is two answers;
   picking the later one is picking by the order the model happened to emit.
3. **Within one prompt.** If two facts in the same batch carry the same id, no ruling in
   that response can be attributed to one of them rather than the other.

All three end the same way, because DESIGN Decision 6's last clause is the only safe
answer to "which fact was this about?": a fact with no ruling we can attribute is
**excluded** with ``low_confidence``.

Nothing here grades a taste JUDGMENT, so nothing here needs an answer key: the sentences
are invented in this file and each assertion is about attribution, not about the ruling
being right.
"""

from __future__ import annotations

import pytest

from arrival.contracts import Fact, Provenance
from arrival.taste import (
    _BATCH_SIZE,
    TasteRuling,
    TasteRulings,
    apply_taste,
    is_displayable,
    rule_verdict,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-4")


#: A literal timestamp. SPEC C7 and determinism both forbid a wall clock in a fixture.
_RETRIEVED_AT = "2026-03-14T09:15:00+00:00"

#: The sentence the classifier rules ``health`` on in batch 1. It is genuinely unsettled by
#: the rule layer (an unexplained absence), which is asserted below rather than assumed.
VICTIM_ID = "rid-victim"
VICTIM_TEXT = (
    "They stepped back from Northwind Labs for eight months in 2023 "
    "and no public source says why."
)


def _fact(fact_id: str, text: str) -> Fact:
    """One displayable-in-principle fact, so exclusion is the only thing under test."""
    return Fact(
        fact_id=fact_id,
        text=text,
        category="hook",
        provenance=Provenance(
            doc_id=f"doc-{fact_id}",
            url=f"https://example.invalid/{fact_id}",
            source_kind="self_page",
            quote=text,
            published_at=None,
            retrieved_at=_RETRIEVED_AT,
            confidence=0.9,
        ),
    )


def _filler(index: int) -> Fact:
    """A distinct sentence that the rule layer also cannot settle.

    Distinct text matters: the classifier double is scripted per CALL, and two identical
    sentences would make it impossible to say which batch a prompt belonged to.
    """
    return _fact(
        f"rid-filler-{index:02d}",
        f"They spent stint {index} away from the Foundry Seed portfolio "
        f"and no public source says why.",
    )


def _rulings(*pairs: tuple[str, str]) -> TasteRulings:
    return TasteRulings(rulings=[TasteRuling(fact_id=fid, verdict=v) for fid, v in pairs])


def test_every_probe_sentence_really_reaches_the_classifier() -> None:
    """Premise of every test below, stated rather than assumed.

    If the rule layer settled these sentences, the tests would be grading the rule layer
    while claiming to grade ruling attribution — a green that is evidence of nothing.
    """
    assert rule_verdict(VICTIM_TEXT).decision == "unsure"
    for index in range(_BATCH_SIZE + 1):
        assert rule_verdict(_filler(index).text).decision == "unsure", index


async def test_a_later_batch_cannot_overwrite_an_earlier_batchs_ruling() -> None:
    """THE REPRODUCTION. R11 material excluded in batch 1, released by a batch-2 echo.

    ``rulings`` accumulates across calls, so an id from batch 2 lands in the same dict as
    batch 1's. The batch-2 response here is not even hostile — a model that repeats a fact
    it saw in the previous prompt produces it — and it silently promotes a ``health``
    exclusion to a ``keep`` that a host reads out loud.
    """
    facts = [_fact(VICTIM_ID, VICTIM_TEXT)]
    facts += [_filler(i) for i in range(_BATCH_SIZE)]  # one more than fits in batch 1
    assert len(facts) == _BATCH_SIZE + 1

    first_batch = facts[:_BATCH_SIZE]
    second_batch = facts[_BATCH_SIZE:]

    llm = LLMDouble()
    llm.queue(
        _rulings(
            (VICTIM_ID, "health"),
            *[(f.fact_id, "keep") for f in first_batch[1:]],
        )
    )
    llm.queue(
        _rulings(
            *[(f.fact_id, "keep") for f in second_batch],
            (VICTIM_ID, "keep"),  # the stray echo of a fact this call never asked about
        )
    )

    results = {f.fact_id: f for f in await apply_taste(facts, llm)}

    assert len(llm.calls) == 2, "the reproduction needs two batches to have happened"
    victim = results[VICTIM_ID]
    assert victim.excluded is True, (
        "a batch-2 ruling naming a batch-1 fact overwrote that fact's exclusion; the health "
        "sentence is now displayable"
    )
    assert victim.exclusion_reason == "health"
    # The harm, stated in the product's own terms rather than narrated. Every other clause
    # of R12 is deliberately satisfied by `_fact` (self_page at 0.9), so the exclusion is
    # the only thing standing between this sentence and a host-facing page.
    assert is_displayable(victim) is False


async def test_a_ruling_for_an_id_never_sent_in_that_call_is_ignored() -> None:
    """The same defect with nothing legitimate alongside it: pure noise, discarded.

    The fact the classifier WAS asked about gets no answer, so it fails closed; the
    invented id changes nothing about any fact.
    """
    fact = _fact(VICTIM_ID, VICTIM_TEXT)
    llm = LLMDouble()
    llm.when(TasteRulings.__name__, "", _rulings(("rid-never-sent", "keep")))

    (result,) = await apply_taste([fact], llm)

    assert llm.calls, "the classifier was never called, so nothing was proven"
    assert result.excluded is True
    assert result.exclusion_reason == "low_confidence"


async def test_two_conflicting_rulings_for_one_id_fail_closed() -> None:
    """A classifier that answered twice, differently, has not answered.

    Taking the last one makes the outcome a function of the order the model emitted its
    list — and half of those orders publish an R11 sentence.
    """
    fact = _fact(VICTIM_ID, VICTIM_TEXT)
    llm = LLMDouble()
    llm.when(TasteRulings.__name__, "", _rulings((VICTIM_ID, "health"), (VICTIM_ID, "keep")))

    (result,) = await apply_taste([fact], llm)

    assert result.excluded is True, "the second of two contradictory rulings released the fact"
    assert result.exclusion_reason == "low_confidence"


async def test_a_repeated_identical_ruling_is_still_one_answer() -> None:
    """The other half of the rule above: repetition is not contradiction.

    A model that lists the same verdict twice has said one thing twice. Failing closed on
    that would throw away a perfectly good answer, so the conflict check must compare
    verdicts rather than count rulings.
    """
    fact = _fact(VICTIM_ID, VICTIM_TEXT)
    llm = LLMDouble()
    llm.when(TasteRulings.__name__, "", _rulings((VICTIM_ID, "keep"), (VICTIM_ID, "keep")))

    (result,) = await apply_taste([fact], llm)

    assert result.excluded is False
    assert result.exclusion_reason is None


async def test_two_facts_sharing_an_id_in_one_batch_both_fail_closed() -> None:
    """An id that is not unique cannot carry a ruling to one fact rather than the other.

    Both sentences appear in the same prompt under the same id, so the single ruling that
    comes back is unattributable. Applying it to both is how one sentence's ``keep``
    becomes another sentence's licence to be displayed.
    """
    shared = "rid-shared"
    innocent = _fact(shared, "They spent a year away from the lab and no public source says why.")
    sensitive = _fact(shared, VICTIM_TEXT)

    llm = LLMDouble()
    llm.when(TasteRulings.__name__, "", _rulings((shared, "keep")))

    results = await apply_taste([innocent, sensitive], llm)

    assert len(results) == 2
    for result in results:
        assert result.excluded is True, "an unattributable ruling released a fact"
        assert result.exclusion_reason == "low_confidence"


async def test_a_blank_id_is_not_an_id() -> None:
    """A model that omits ``fact_id`` must not thereby address a fact.

    ``Fact.fact_id`` carries no minimum length and a response field that is missing reads
    as the empty string, so without this an empty id would be the one id a model gets for
    free — and it would land on whichever fact happened to have a blank id.
    """
    blank = _fact("", VICTIM_TEXT)
    llm = LLMDouble()
    llm.when(TasteRulings.__name__, "", _rulings(("", "keep"), ("   ", "keep")))

    (result,) = await apply_taste([blank], llm)

    assert result.excluded is True
    assert result.exclusion_reason == "low_confidence"


async def test_a_rule_settled_fact_is_never_reopened_by_a_stray_ruling() -> None:
    """Defence in depth, and a pin on behaviour that is already correct.

    A fact the rule layer excluded outright is never sent to the classifier, so a ruling
    naming its id is by construction about a fact this call did not ask about. It must not
    be able to reach the fact by any route.
    """
    settled_id = "rid-rule-settled"
    settled = _fact(settled_id, "Their diagnosis in 2021 was described in a hospital letter.")
    assert rule_verdict(settled.text).decision == "exclude"

    unsure = _fact(VICTIM_ID, VICTIM_TEXT)
    llm = LLMDouble()
    llm.when(
        TasteRulings.__name__,
        "",
        _rulings((settled_id, "keep"), (VICTIM_ID, "keep")),
    )

    results = {f.fact_id: f for f in await apply_taste([settled, unsure], llm)}

    assert results[settled_id].excluded is True
    assert results[settled_id].exclusion_reason == "health"
    assert results[VICTIM_ID].excluded is False
