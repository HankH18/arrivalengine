"""T-048/T-049: `_best`'s refusal is a last resort, and two extractors were making it common.

`_best` refuses to mint a strong key when the top two candidates are different values with
identical evidence. That refusal is correct and is asserted here to still fire. What was
wrong is how OFTEN it fired, and in both cases the cause was upstream of the tie:

* **`company_domain` ranked on nothing.** Every host scored `1`, so the only signal left
  was how many documents each host happened to get — chosen by the same remote rankings
  the refusal exists to distrust — and one `.com` against one `.io` refused on input that
  is not ambiguous at all.
* **`sec_cik` read the wrong number.** An ownership filing names the reporting owner AND
  the issuer and gives each a CIK; taking the first one on the page read whichever entity
  EDGAR listed first, so two filings about one human produced two values and neither
  survived. `research._by_display_priority` keeping the edgar-stamped copy of a page
  `search` also indexed — which is its purpose and is right — made that arrive more often.

ANSWER KEYS, and why nothing here grades against the lane's own files. The EDGAR documents
are rendered by `arrival.connectors.edgar.EdgarConnector._document` from EDGAR-shaped hits,
so their title and body are the CONNECTOR's rendering, not a shape written to suit the
resolver; the CIK each assertion expects is the one EDGAR's own `display_names` puts beside
the person's name. The merge's expected winner is `arrival.connectors.DISPLAY_PRIORITY`'s
ranking of `edgar` above `search`. Both modules are outside this lane's ownership. The
domain expectations are properties of the URL host written into each input, and the
permutation properties are agreements between two runs of the gradee with no key at all.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from arrival.connectors import DISPLAY_PRIORITY
from arrival.connectors.edgar import EdgarConnector
from arrival.contracts import PersonRef, RawDoc
from arrival.research import _by_display_priority
from arrival.resolve import strong_keys_for
from arrival.util import doc_id as make_doc_id

pytestmark = pytest.mark.ticket("T-2")

FETCHED = dt.datetime(2026, 3, 2, 11, 0, tzinfo=dt.UTC)

PERSON = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=["CFO at Harrowgate Systems", "Austin"],
)

#: A page that names the person, her employer and her city — the leadership page a company
#: keeps on its own domain.
NAMES_HER = (
    "Dara Whitfield is Chief Financial Officer of Harrowgate Systems and works from Austin."
)
#: A page that spells the company's name and nothing else about her — a docs site.
NAMES_ONLY_THE_COMPANY = "Harrowgate Systems developer documentation. Getting started."


def page(url: str, text: str, kind: str = "search") -> RawDoc:
    return RawDoc(
        doc_id=make_doc_id(url),
        source_kind=kind,
        url=url,
        title="",
        text=text,
        fetched_at=FETCHED,
    )


def domain(docs: list[RawDoc]) -> str:
    return strong_keys_for(PERSON, docs).get("company_domain", "")


def cik(docs: list[RawDoc]) -> str:
    return strong_keys_for(PERSON, docs).get("sec_cik", "")


# ------------------------------------------------------------------ T-048


@pytest.mark.parametrize(
    ("rich_host", "bare_host"),
    [
        ("harrowgatesystems.com", "harrowgatesystems.io"),
        ("harrowgatesystems.io", "harrowgatesystems.com"),
        ("harrowgatesystems.com", "harrowgatesystems.co.uk"),
        ("harrowgatesystems.co.uk", "harrowgatesystems.com"),
        ("harrowgatesystems.com", "harrowgatesystems.github.io"),
        ("harrowgatesystems.github.io", "harrowgatesystems.com"),
    ],
)
def test_the_host_whose_page_names_the_person_wins_whichever_tld_it_is(
    rich_host: str, bare_host: str
) -> None:
    """Evidence decides, and the roles are swapped in every pair so a TLD cannot.

    Each host appears once as the informative page and once as the bare one. A rule that
    preferred `.com`, or the shorter host, or the first document, fails half of these.
    """
    rich = page(f"https://{rich_host}/team", NAMES_HER)
    bare = page(f"https://{bare_host}/docs", NAMES_ONLY_THE_COMPANY)
    forwards, backwards = domain([rich, bare]), domain([bare, rich])
    assert forwards == backwards, (
        f"arrival order changed the company_domain: {forwards} vs {backwards}"
    )
    assert forwards == rich_host, (
        f"the page naming the person, her employer and her city sits on {rich_host}; "
        f"the bare docs host {bare_host} must not outrank it (got {forwards!r})"
    )


def test_a_domain_is_not_decided_by_how_many_documents_a_host_happened_to_get() -> None:
    """One informative page beats two uninformative ones.

    Document count is the remote APIs' ranking in disguise: it is how many results each
    engine chose to return. It is the LAST tie-break, never the first.
    """
    rich = page("https://harrowgatesystems.com/team", NAMES_HER)
    bare_one = page("https://harrowgatesystems.io/docs", NAMES_ONLY_THE_COMPANY)
    bare_two = page("https://harrowgatesystems.io/guide", NAMES_ONLY_THE_COMPANY)
    assert domain([rich, bare_one, bare_two]) == "harrowgatesystems.com"
    assert domain([bare_two, bare_one, rich]) == "harrowgatesystems.com"


def test_two_hosts_nothing_can_separate_still_mint_no_domain() -> None:
    """The refusal is not deleted: equal evidence on two different values is a guess."""
    one = page("https://harrowgatesystems.com/docs", NAMES_ONLY_THE_COMPANY)
    two = page("https://harrowgatesystems.io/docs", NAMES_ONLY_THE_COMPANY)
    assert domain([one, two]) == "", (
        "two hosts with identical evidence are two candidate employers; picking either is "
        "arrival order wearing the costume of evidence"
    )


def test_a_subdomain_of_the_winning_host_is_still_the_same_one_identifier() -> None:
    """Canonicalisation must survive the evidence scoring, not be undone by it."""
    apex = page("https://harrowgatesystems.com/team", NAMES_HER)
    blog = page("https://blog.harrowgatesystems.com/2025/notes", NAMES_ONLY_THE_COMPANY)
    for order in itertools.permutations([apex, blog]):
        assert domain(list(order)) == "harrowgatesystems.com"


# ------------------------------------------------------------------ T-049

HER_CIK = "0001742119"
ISSUER_CIK = "0009876543"
OTHER_PERSONS_CIK = "0007000777"

_connector = EdgarConnector()


def filing(adsh: str, display_names: list[str]) -> RawDoc:
    """A Form 4 rendered by the EDGAR connector itself, from an EDGAR-shaped hit.

    Going through `_document` rather than writing the text by hand is the point: the shape
    under test is the one the product actually produces, including the fact that BOTH the
    reporting owner's CIK and the issuer's land in the title and the body.
    """
    rendered = _connector._document(
        {
            "_source": {
                "adsh": adsh,
                "ciks": [HER_CIK],
                "display_names": display_names,
                "form": "4",
                "root_forms": ["4"],
                "file_date": "2024-04-18",
                "file_description": (
                    "FORM 4 - statement of changes in beneficial ownership. Dara Whitfield "
                    "is Chief Financial Officer of Harrowgate Systems in Austin."
                ),
            }
        }
    )
    assert rendered is not None, "pre-condition: the connector must render this hit"
    return rendered


HER_ENTRY = f"Whitfield Dara (CIK {HER_CIK})"
ISSUER_ENTRY = f"Harrowgate Systems Inc (CIK {ISSUER_CIK})"

PERSON_LISTED_FIRST = filing("0001742119-24-000012", [HER_ENTRY, ISSUER_ENTRY])
ISSUER_LISTED_FIRST = filing("0001742119-24-000031", [ISSUER_ENTRY, HER_ENTRY])


def test_the_rendered_filing_really_does_carry_both_ciks() -> None:
    """Premise check: without two CIKs on one page there is nothing here to get wrong."""
    body = f"{PERSON_LISTED_FIRST.title}\n{PERSON_LISTED_FIRST.text}"
    assert HER_CIK in body and ISSUER_CIK in body, (
        "the connector's rendering must contain the reporting owner's CIK and the "
        f"issuer's; got {body!r}"
    )
    assert body.index(HER_CIK) < body.index(ISSUER_CIK)
    other = f"{ISSUER_LISTED_FIRST.title}\n{ISSUER_LISTED_FIRST.text}"
    assert other.index(ISSUER_CIK) < other.index(HER_CIK), (
        "the second filing must list the issuer first, or the two orderings are the same "
        "test written twice"
    )


def test_the_cik_is_the_one_edgar_puts_beside_her_name_not_the_first_on_the_page() -> None:
    """Which entity EDGAR listed first is not evidence about which human this is."""
    assert cik([ISSUER_LISTED_FIRST]) == HER_CIK, (
        f"the issuer's CIK {ISSUER_CIK} was taken from a filing whose own display_names "
        f"give {HER_ENTRY}"
    )
    assert cik([PERSON_LISTED_FIRST]) == HER_CIK


def test_two_filings_listing_the_entities_differently_agree_on_her_cik() -> None:
    """The T-049 loss: one human, two filings, and the key used to vanish between them."""
    forwards = cik([PERSON_LISTED_FIRST, ISSUER_LISTED_FIRST])
    backwards = cik([ISSUER_LISTED_FIRST, PERSON_LISTED_FIRST])
    assert forwards == backwards == HER_CIK, (
        f"two filings naming one person produced {forwards!r}/{backwards!r}; the corpus "
        "states her CIK twice and the resolver must not refuse it"
    )


def test_the_priority_merge_stamping_a_second_copy_edgar_does_not_cost_the_key() -> None:
    """The composition the ticket names, with the merge doing exactly its job.

    `search` also indexed the second filing. `_by_display_priority` keeps the edgar-stamped
    copy — `DISPLAY_PRIORITY` ranks `edgar` above `search` — which is correct and which is
    what puts two edgar documents in front of `_sec_cik`. The key must survive that.
    """
    assert DISPLAY_PRIORITY.index("edgar") < DISPLAY_PRIORITY.index("search"), (
        "premise: the project ranks edgar above search, so the edgar copy is the survivor"
    )
    search_copy = ISSUER_LISTED_FIRST.model_copy(update={"source_kind": "search"})
    winners = _by_display_priority([[PERSON_LISTED_FIRST, ISSUER_LISTED_FIRST], [search_copy]])
    merged = [winners[PERSON_LISTED_FIRST.doc_id], winners[ISSUER_LISTED_FIRST.doc_id]]

    assert [d.source_kind for d in merged] == ["edgar", "edgar"], (
        "pre-condition: the merge must produce the two-edgar corpus the ticket describes"
    )
    assert cik(merged) == HER_CIK


def test_two_different_ciks_both_attributed_to_her_still_mint_nothing() -> None:
    """The refusal survives where it is earned: a contradiction is not a choice."""
    impostor = filing(
        "0001742119-24-000044",
        [f"Whitfield Dara (CIK {OTHER_PERSONS_CIK})", ISSUER_ENTRY],
    )
    assert cik([PERSON_LISTED_FIRST, impostor]) == "", (
        "two filings attributing different CIKs to the same name are two candidate humans"
    )
    assert cik([impostor, PERSON_LISTED_FIRST]) == ""


def test_every_arrival_order_of_the_edgar_corpus_agrees() -> None:
    corpus = [PERSON_LISTED_FIRST, ISSUER_LISTED_FIRST]
    assert {cik(list(order)) for order in itertools.permutations(corpus)} == {HER_CIK}
