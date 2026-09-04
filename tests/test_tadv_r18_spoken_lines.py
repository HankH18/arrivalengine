"""R18 defects in the lines a host reads OUT LOUD, measured on the real ten-person corpus.

R18's three spoken lines are the ``who_line``, every Meet ``why``, and the opener. They
are the product: a host has ninety seconds in a lobby and reads them to the member who
just walked in. ``digest.is_speakable`` already guards the MECHANICAL hazards — URLs,
``[n]`` markers, parentheses, bare scores, the 30-word cap, a spliced clause. It does not
guard two things that are just as audible, and both are live on the committed corpus:

**1. A line that does not end in a sentence.** ``OPENER_TEMPLATE`` interpolates a fact's
text verbatim and ``who_line_for`` concatenates facts verbatim, and a ``Fact.text`` is not
required to carry terminal punctuation. ``nabeel-qureshi``'s Who line ends "…Helped build
GoCardless, a payments company in the UK" — the host runs off the end of the sentence.

**2. A line that says the same thing twice inside a 30-word budget.** ``who_line_for``
packs facts greedily up to ``SPOKEN_WORD_CAP`` and never asks whether the next fact adds
anything. ``steve-huffman``'s Who line spends its last eleven words on "Steve Huffman is
co-founder and CEO of Reddit. Steve Huffman is the co-founder and CEO of Reddit." —
the same claim, twice, differing by the word "the", read aloud to Steve Huffman.
``hunter-walk`` does the same with "Partner at Homebrew". Three of the ten Who lines say
the member's full name four times in thirty words.

The redundancy detector below is deliberately conservative: two sentences are the same
claim only when one's content words (stop words and the member's own name removed) are a
SUBSET of the other's. Measured over all ten committed dossiers it fires on exactly the
two Who lines above and on nothing else, so it reports a real defect rather than a style
preference.

On the strict-xfail markers, see the module docstring of
``test_tadv_r11_hub_label_bypass.py``.
"""

from __future__ import annotations

import re

import pytest

from arrival.digest import SPOKEN_WORD_CAP, is_speakable, who_line_for
from tadv_corpus import committed_dossiers

pytestmark = pytest.mark.ticket("TESTADVERSARY")

TERMINAL = (".", "!", "?", '."', ".'", '.”')

#: Removed before comparing two sentences for redundancy. Function words only — nothing
#: that could carry the claim.
_STOP = frozenset(
    "a an the of and or in at on for to with is are was were be been as by from "
    "his her their its this that not no".split()
)


def _who_lines():
    return [(d.person, who_line_for(d)[0]) for d in committed_dossiers()]


def _sentences(line: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", line.strip()) if s.strip()]


def _content_words(sentence: str, name: str) -> frozenset[str]:
    stripped = sentence.lower().replace(name.lower(), " ")
    return frozenset(
        word
        for word in re.findall(r"[a-z0-9][a-z0-9'\-]*", stripped)
        if word not in _STOP and len(word) > 2
    )


# --------------------------------------------------------------------------- what works

def test_the_corpus_supplies_a_who_line_for_every_member():
    """Positive control: every check below would pass vacuously on an empty corpus."""
    lines = _who_lines()
    assert len(lines) >= 10, lines
    assert all(line.strip() for _person, line in lines)


def test_every_who_line_is_within_the_spoken_word_cap():
    """R18's hard cap, which `is_speakable` does enforce. Locked so it cannot slip."""
    for person, line in _who_lines():
        assert len(line.split()) <= SPOKEN_WORD_CAP, (person.person_id, line)


def test_every_who_line_passes_the_mechanical_speakability_gate():
    """No URL, no `[n]` marker, no parenthetical, no bare score."""
    for person, line in _who_lines():
        assert is_speakable(line), (person.person_id, line)


# --------------------------------------------------------------------------- what breaks

@pytest.mark.xfail(
    strict=True,
    reason="OPEN R18 DEFECT: who_line_for concatenates Fact.text verbatim and a fact need "
    "not end in a full stop, so a spoken line can run off the end of its last sentence. "
    "Remove this marker when it is fixed.",
)
def test_every_who_line_ends_in_a_finished_sentence():
    """A line read aloud has to end. `is_speakable` does not check this today."""
    offenders = [
        f"{person.person_id}: ...{line[-70:]!r}"
        for person, line in _who_lines()
        if not line.rstrip().endswith(TERMINAL)
    ]
    assert offenders == [], "\n" + "\n".join(offenders)


@pytest.mark.xfail(
    strict=True,
    reason="OPEN R18 DEFECT: who_line_for packs facts greedily to SPOKEN_WORD_CAP without "
    "checking whether the next fact repeats one already in the line. Remove this marker "
    "when it is fixed.",
)
def test_no_who_line_states_the_same_claim_twice():
    """Thirty words is the whole budget; spending eleven of them on a repeat is a defect."""
    offenders = []
    for person, line in _who_lines():
        sentences = [s for s in _sentences(line) if _content_words(s, person.name)]
        for i, first in enumerate(sentences):
            for second in sentences[i + 1 :]:
                a = _content_words(first, person.name)
                b = _content_words(second, person.name)
                if a <= b or b <= a:
                    offenders.append(f"{person.person_id}: {first!r} ~~ {second!r}")
    assert offenders == [], "\n" + "\n".join(offenders)


@pytest.mark.xfail(
    strict=True,
    reason="OPEN R18 DEFECT: three of the ten Who lines say the member's full name four "
    "times in thirty words. Remove this marker when it is fixed.",
)
def test_no_who_line_repeats_the_members_full_name_more_than_three_times():
    """A host saying "Steve Huffman" four times in thirty words is reading a database."""
    offenders = [
        f"{person.person_id}: name x{line.count(person.name)} in {len(line.split())} words"
        for person, line in _who_lines()
        if line.count(person.name) > 3
    ]
    assert offenders == [], "\n" + "\n".join(offenders)
