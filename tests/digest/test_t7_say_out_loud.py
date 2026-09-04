"""T-7 acceptance 3 (R14, DESIGN Decision 12): one bounded LLM call, always answered.

The opener is the only generated sentence on the page. It comes from exactly ONE
``llm.structured`` call, bounded at 2.5 s, and its output is validated as an invitation.
Timeout, transport failure and a model that ignored the brief all land on the documented
template ``f"Ask about {hook.text}"``.

The delays here are real ``asyncio.sleep`` — ``LLMDouble(delay=…)`` exists for exactly this
— but the test costs the 2.5 s BUDGET, not the delay, because the implementation's own
``wait_for`` cancels the call. If the timeout were missing these tests would hang rather
than quietly pass, which is the failure direction worth having.
"""

from __future__ import annotations

import time

import pytest
from t7_digest_helpers import fact_of, load, replacing, variant, with_facts

from arrival.contracts import LLMError
from arrival.digest import (
    OPENER_OF_LAST_RESORT,
    SAY_OUT_LOUD_TIMEOUT_SECONDS,
    SayOutLoud,
    is_speakable,
    make_digest,
    pick_opener_hook,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-7")

GOOD_LINE = "Ask about the nine months of rubric work before the first line of code."


def _templated(dossier, fact_id="alpha-hook"):
    """The documented fallback, `f"Ask about {hook.text}"`, read off the fixture.

    Read rather than transcribed on purpose: a hand-copied sentence in a test grades the
    transcription, and the assertion that matters here is WHICH fact was chosen, which the
    `fact_id` argument still pins exactly.
    """
    return f"Ask about {fact_of(dossier, fact_id).text}"


@pytest.fixture
def alpha():
    return load("alpha")


async def test_say_out_loud_shape(alpha):
    """A valid invitation is used verbatim, and costs exactly one structured call."""
    llm = LLMDouble()
    llm.queue({"line": GOOD_LINE})

    digest = await make_digest(alpha, [], llm)

    assert digest.say_out_loud == GOOD_LINE
    assert llm.call_count == 1, (
        f"the opener must be ONE llm.structured call; made {llm.call_count}"
    )
    call = llm.calls[0]
    assert call.schema_name == SayOutLoud.__name__
    assert call.system.strip(), "the call carried no system prompt"
    assert alpha.person.name in call.user, "the prompt never named the arriving member"


async def test_say_out_loud_fallback(alpha):
    """DESIGN Decision 12: a slow model yields the templated highest-confidence hook."""
    llm = LLMDouble(delay=SAY_OUT_LOUD_TIMEOUT_SECONDS + 30.0)
    llm.queue({"line": GOOD_LINE})

    started = time.monotonic()
    digest = await make_digest(alpha, [], llm)
    elapsed = time.monotonic() - started

    assert digest.say_out_loud == _templated(alpha)
    assert llm.call_count == 1, "the builder retried a call it had already given up on"
    assert elapsed < SAY_OUT_LOUD_TIMEOUT_SECONDS + 5.0, (
        f"the builder waited {elapsed:.1f}s on a call it budgets {SAY_OUT_LOUD_TIMEOUT_SECONDS}s "
        "for; the deadline is not being enforced"
    )


async def test_a_call_just_inside_the_budget_is_still_used(alpha):
    """The deadline is a deadline, not a blanket refusal to wait."""
    llm = LLMDouble(delay=0.25)
    llm.queue({"line": GOOD_LINE})

    digest = await make_digest(alpha, [], llm)

    assert digest.say_out_loud == GOOD_LINE


@pytest.mark.parametrize(
    "rogue",
    [
        "I saw that you have been writing about evaluation harnesses.",
        "We noticed your rubric work.",
        "Our records show nine months of rubric work.",
        "You spent nine months on a scoring rubric.",  # not an invitation at all
        "Ask about the rubric (nine months of it).",  # R18: parenthetical
        "Ask about the rubric at https://example.com/rubric.",  # R18: URL
        "Ask about the rubric [1].",  # R18: citation marker
    ],
)
async def test_a_line_that_breaks_r14_or_r18_is_replaced_by_the_template(alpha, rogue):
    llm = LLMDouble()
    llm.queue({"line": rogue})

    digest = await make_digest(alpha, [], llm)

    assert llm.call_count == 1, "the builder asked the model twice for one line"
    assert digest.say_out_loud == _templated(alpha)


async def test_an_over_long_line_is_replaced_rather_than_truncated(alpha):
    """R18 caps the spoken line at thirty words; a generated line is regenerated, not cut."""
    llm = LLMDouble()
    llm.queue({"line": "Ask about " + " ".join(["rubrics"] * 40) + "."})

    digest = await make_digest(alpha, [], llm)

    assert digest.say_out_loud == _templated(alpha)


async def test_a_failing_llm_still_yields_an_opener(alpha):
    """R3: the arrival path has no branch that leaves the host with nothing to say."""
    llm = LLMDouble()
    llm.queue(LLMError("the API is down"))

    digest = await make_digest(alpha, [], llm)

    assert digest.say_out_loud == _templated(alpha)


async def test_an_unscripted_double_is_a_failure_the_builder_absorbs(alpha):
    """``LLMDouble`` raises ``LLMError`` on an unscripted call — the transport-failure path."""
    llm = LLMDouble()

    digest = await make_digest(alpha, [], llm)

    assert llm.call_count == 1
    assert digest.say_out_loud.startswith("Ask about ")


def test_the_fallback_hook_is_the_highest_confidence_displayable_hook(alpha):
    hooks = [f for f in alpha.facts if f.category == "hook"]
    assert hooks, "fixture changed: alpha has no hook fact"

    chosen = pick_opener_hook(alpha)

    assert chosen is not None
    assert chosen.fact_id == "alpha-hook"


def test_an_undisplayable_hook_is_never_the_fallback(alpha):
    """R12 gates the fallback too, or a low-confidence hook becomes the spoken line."""
    doomed = variant(fact_of(alpha, "alpha-hook"), confidence=0.55)
    dossier = replacing(alpha, {"alpha-hook": doomed})

    chosen = pick_opener_hook(dossier)

    assert chosen is not None
    assert chosen.fact_id != "alpha-hook"


def test_with_no_hook_fact_the_fallback_is_the_most_recent_displayable_fact(alpha):
    dossier = with_facts(alpha, [f for f in alpha.facts if f.category != "hook"])

    chosen = pick_opener_hook(dossier)

    assert chosen is not None
    assert chosen.fact_id == "alpha-interest", "expected the newest displayable fact"


async def test_with_nothing_displayable_the_opener_is_still_an_invitation(alpha):
    """An empty dossier must not produce an empty spoken line."""
    dossier = with_facts(alpha, [])
    assert pick_opener_hook(dossier) is None
    llm = LLMDouble()

    digest = await make_digest(dossier, [], llm)

    assert digest.say_out_loud == OPENER_OF_LAST_RESORT
    assert digest.say_out_loud.startswith("Ask")


async def test_curious_is_accepted_as_an_invitation(alpha):
    line = "Curious what nine months on a scoring rubric teaches you about harnesses."
    llm = LLMDouble()
    llm.queue({"line": line})

    digest = await make_digest(alpha, [], llm)

    assert digest.say_out_loud == line


async def test_the_fallback_opener_never_reads_back_the_who_line(alpha):
    """Taste, measured: without this the host reads one sentence twice.

    Four of the five people in the frozen grading corpus carry no ``hook`` fact, and their
    highest-confidence displayable fact is the one the Who line already speaks. The
    documented fallback ("the most recent displayable fact if none") therefore has to skip
    what has already been said, or the page opens by repeating itself.
    """
    dossier = with_facts(alpha, [f for f in alpha.facts if f.category != "hook"])
    llm = LLMDouble()  # unscripted: the transport-failure path, so the fallback is used

    digest = await make_digest(dossier, [], llm)

    assert digest.say_out_loud.startswith("Ask about ")
    spoken = digest.say_out_loud[len("Ask about "):]
    assert spoken not in digest.who_line, (
        f"the opener reads the Who line back: {digest.who_line!r} / {digest.say_out_loud!r}"
    )


def test_the_hook_choice_ignores_facts_already_spoken(alpha):
    """``exclude`` narrows the candidate pool; it never empties it while facts remain."""
    dossier = with_facts(alpha, [f for f in alpha.facts if f.category != "hook"])
    unfiltered = pick_opener_hook(dossier)
    assert unfiltered is not None

    filtered = pick_opener_hook(dossier, exclude=[unfiltered])

    assert filtered is not None
    assert filtered.fact_id != unfiltered.fact_id


def test_the_deadline_is_the_one_design_decision_12_specifies():
    """Pin the LITERAL 2.5 s.

    Every other timing assertion in this module is written in terms of the constant, so
    all of them stay green if the constant is widened to 250. This is the one that does not.
    """
    assert SAY_OUT_LOUD_TIMEOUT_SECONDS == 2.5


def test_the_fallback_hook_is_the_HIGHEST_confidence_of_several(alpha):
    """The fixture carries one hook, so "highest" needs a second one to mean anything.

    Measured: with a single-hook fixture, flipping `max` to `min` in the selection left
    this module entirely green.
    """
    from t7_digest_helpers import with_facts

    weaker = variant(fact_of(alpha, "alpha-hook"), confidence=0.72)
    stronger = variant(
        weaker,
        fact_id="alpha-hook-strong",
        text="He rewrote the rubric three times before anyone else saw it.",
        confidence=0.97,
        doc_id="00000000000000d1",
    )
    dossier = replacing(alpha, {"alpha-hook": weaker})
    dossier = with_facts(dossier, [*dossier.facts, stronger])

    chosen = pick_opener_hook(dossier)

    assert chosen is not None
    assert chosen.fact_id == "alpha-hook-strong", "the lower-confidence hook was chosen"


@pytest.mark.parametrize(
    "rough_fact_text",
    [
        "Believes pricing pages (all of them) belong on the web.",
        "Believes pricing belongs at https://example.org/pricing for everyone.",
        "Believes pricing should be public, scored 67 by the internal ranker.",
        "Believes " + " ".join(["pricing"] * 40) + ".",
    ],
)
async def test_the_templated_opener_is_held_to_r18_too(alpha, rough_fact_text):
    """R18 binds `say_out_loud` on EVERY path, and the template is the failure path.

    `f"Ask about {hook.text}"` interpolates a fact verbatim, and a fact is not guaranteed
    speakable — they run to 200 characters and nothing stops one carrying a parenthetical
    or a URL. Validating only the MODEL's line leaves R18 unenforced on the path taken by
    timeout, transport error and every rejected model line, which is the path DESIGN
    Decision 12 exists to make reliable.
    """
    rough = variant(fact_of(alpha, "alpha-hook"), text=rough_fact_text)
    dossier = replacing(alpha, {"alpha-hook": rough})
    llm = LLMDouble()  # unscripted: the template branch is taken

    digest = await make_digest(dossier, [], llm)

    assert is_speakable(digest.say_out_loud), (
        f"the fallback opener is not speakable: {digest.say_out_loud!r}"
    )
    assert rough_fact_text not in digest.say_out_loud, "the unspeakable hook was used anyway"
    assert digest.say_out_loud.startswith(("Ask", "Curious"))


async def test_when_no_candidate_survives_r18_the_last_resort_opener_is_used(alpha):
    """Skipping to the next hook is not a licence to ship nothing."""
    from t7_digest_helpers import with_facts

    unspeakable = [
        variant(f, text=f"Does things at https://example.org/{f.fact_id} always.")
        for f in alpha.facts
    ]
    dossier = with_facts(alpha, unspeakable)
    llm = LLMDouble()

    digest = await make_digest(dossier, [], llm)

    assert digest.say_out_loud == OPENER_OF_LAST_RESORT
    assert is_speakable(digest.say_out_loud)
