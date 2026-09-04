"""One definition of "is this document about THIS person?", shared by all ten connectors.

WHY THIS MODULE EXISTS AT ALL.  Four connectors were repaired one at a time for the same
defect — **a name match treated as an identity match** — and each repair shipped its own
private copy of `_tokens` / `_carries_name`, fifteen lines duplicated four times, because
`base.py` sat outside the lane that was fixing them.  A sibling sweep then found six more
connectors carrying the same defect.  Fixing those six individually would have made
instances seven through twelve of one bug.  Four identical copies of a predicate is not
tidiness debt; it is the shape of an abstraction that was missing, and the sixth instance
of a bug is evidence about the contract, not about the connector.

THE CONTRACT, IN ONE SENTENCE.  A connector may emit a document about a person only when
the response it came from carries evidence tying that item to *this* person — the
member's own domain, or their full name together with at least one detail the roster
independently supplied.  A bare name match is a **no**.

WHY REFUSAL IS THE DEFAULT.  The cost function is asymmetric and the asymmetry is not
close.  A document wrongly declined costs one paragraph of one dossier.  A stranger
wrongly accepted is written into the hub graph, where T-5 joins every other person on the
roster onto it — so one false positive contaminates the matching for *everybody*, keeps
producing confident matches off the merge, and is invisible in a digest that looks exactly
like a correct one.  So: **unverifiable is treated as no.**  A connector declines by
returning `[]` (`Connector.search` must never raise — DESIGN Decision 8), never by
guessing.

WHY A NAME IS NOT AN IDENTIFIER, CONCRETELY.  Every source here answers a *string* query
against a full-text index that has no notion of people: EDGAR full-text search is not
fielded, HN's Algolia backend is typo-tolerant and does not honour `"` as a strict phrase
operator, GitHub's `/search/users` ranks by popularity, and a search engine returns
whatever it thinks you meant.  "the API returned it for my query" is therefore evidence
about the query, not about the person.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from urllib.parse import urlsplit

from arrival.connectors.base import affiliations, hosts_in, urls_in
from arrival.contracts import PersonRef
from arrival.util import normalize_ws

__all__ = [
    "SHARED_HOSTS",
    "US_STATES",
    "best_affiliation",
    "carries_name",
    "choose_one",
    "corroborates",
    "corroboration",
    "identifies",
    "is_an_address",
    "is_shared_host",
    "mentions_name",
    "on_own_host",
    "own_hosts",
    "roster_terms",
    "tokens",
]

_WORD = re.compile(r"[^0-9a-z]+")

#: Names and postal codes of the US states, plus DC. A `detail` ending in one of these is
#: an address. An address is fine as CORROBORATION ("Providence" in a GitHub location
#: field is real evidence) and useless as a SEARCH TERM: a city searched against a name
#: index returns that city's residents, none of whom the member need have met.
US_STATES = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "district of columbia", "florida", "georgia",
        "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
        "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
        "new jersey", "new mexico", "new york", "north carolina", "north dakota",
        "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
        "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
        "washington", "west virginia", "wisconsin", "wyoming",
        "al", "ak", "az", "ar", "ca", "co", "ct", "dc", "de", "fl", "ga", "hi", "ia",
        "id", "il", "in", "ks", "ky", "la", "ma", "md", "me", "mi", "mn", "mo", "ms",
        "mt", "nc", "nd", "ne", "nh", "nj", "nm", "nv", "ny", "oh", "ok", "or", "pa",
        "ri", "sc", "sd", "tn", "tx", "ut", "va", "vt", "wa", "wi", "wv", "wy",
    }
)

#: Domains where a URL names a PAGE and never a person. A roster line pointing at one of
#: these is a link to one profile among millions on the same host, so anything that keys
#: on the HOST alone — a wayback `{host}/*` enumeration, an "is this their own site?"
#: check — silently widens from the member to the whole platform. Deliberately a small,
#: legible list of the platforms a club roster actually contains rather than an attempt at
#: a public-suffix database: an unlisted platform costs one over-broad query, and the path
#: check below still applies wherever the roster gave a path.
SHARED_HOSTS = frozenset(
    {
        "about.me", "angel.co", "behance.net", "blogspot.com", "bsky.app",
        "crunchbase.com", "dribbble.com", "facebook.com", "gitlab.com", "github.com",
        "github.io", "instagram.com", "linkedin.com", "medium.com", "mastodon.social",
        "notion.site", "patreon.com", "reddit.com", "sites.google.com", "soundcloud.com",
        "stackoverflow.com", "substack.com", "threads.net", "tumblr.com", "twitter.com",
        "vimeo.com", "wordpress.com", "x.com", "youtube.com",
    }
)

# -- the primitives ---------------------------------------------------------------------


def tokens(text: str) -> tuple[str, ...]:
    """Comparable word tokens, IN ORDER. Single letters are dropped: initials match all.

    Order is kept — the four repaired connectors only ever needed the set — because
    `mentions_name` has to ask whether the words are ADJACENT, and a set cannot answer
    that. `carries_name` takes the set of this and is bit-for-bit the predicate those four
    shipped.
    """
    return tuple(word for word in _WORD.split(normalize_ws(text)) if len(word) >= 2)


def carries_name(label: str, name: str) -> bool:
    """True when every word of `name` appears in `label`. The LABEL test.

    A superset is allowed on purpose — "Pell Marrowby (entrepreneur)" and "Marisol
    Quennebeck Vidal" are both plausibly her, and real 990 rosters are written
    `QUENNEBECK MARISOL A` about as often as `Marisol Quennebeck` — while "Pelmyre Works"
    is not.  Containment also means a shared surname alone is never a match.

    Use this on a field whose whole job is to NAME ONE ENTITY: a Wikidata label, an
    OpenAlex `display_name`, an EDGAR `display_names` entry, a GitHub profile's `name`.
    For prose use `mentions_name` — a page reading "Marisol Farrow met Dev Quennebeck"
    contains every word of her name and is about two other people.
    """
    wanted = set(tokens(name))
    return bool(wanted) and wanted <= set(tokens(label))


def mentions_name(text: str, name: str) -> bool:
    """True when `name` appears in `text` as a CONTIGUOUS run of words. The PROSE test.

    Strictly stronger than `carries_name`, and the right question to ask of a snippet, a
    title, a bio or an archived page, where the words of a name can co-occur for reasons
    that have nothing to do with the person.
    """
    wanted = tokens(name)
    if not wanted:
        return False
    haystack = tokens(text)
    span = len(wanted)
    return any(haystack[i : i + span] == wanted for i in range(len(haystack) - span + 1))


def is_an_address(detail: str) -> bool:
    """`"Providence, Rhode Island"` yes; `"co-founder, Thornfield Loom"` no."""
    normalised = normalize_ws(detail)
    if normalised in US_STATES:
        return True
    fragments = [fragment.strip() for fragment in detail.split(",")]
    return len(fragments) >= 2 and normalize_ws(fragments[-1]) in US_STATES


# -- what the roster independently supplied ----------------------------------------------


def roster_terms(person: PersonRef) -> list[str]:
    """Every detail-derived phrase that can CORROBORATE a hit, normalised for comparison.

    Places are kept here and dropped by `best_affiliation`: a city is worthless as a query
    term and valuable as a check, because a profile that volunteers "Providence" was not
    asked to.
    """
    return [term for term in (normalize_ws(t) for t in affiliations(person.details)) if term]


def best_affiliation(person: PersonRef) -> str:
    """The strongest ORGANISATION in `details` to put in a query, or `""`.

    `affiliations()` returns detail order, so `next(iter(affiliations(...)))` — which is
    what four connectors were doing — hands the search engine a CITY whenever the roster
    happens to list the city first, and "Marisol Quennebeck Providence" is a query about a
    city.  Addresses are skipped here and only here; `roster_terms` still checks them.
    """
    terms = affiliations(person.details)
    for term in terms:
        if normalize_ws(term) not in US_STATES and not is_an_address(term):
            return term
    return next(iter(terms), "")


def own_hosts(person: PersonRef) -> list[str]:
    """The hostnames the roster itself gave for this person."""
    return hosts_in(person.details)


def is_shared_host(host: str) -> bool:
    """True when this domain is a PLATFORM many people publish under, not one person's.

    `thornfieldloom.example.com` belongs to one member; `linkedin.com` belongs to nine
    hundred million.  A roster line reading `https://www.linkedin.com/in/marisol-...`
    identifies a PAGE, and treating it as identifying a HOST turns "her own site" into
    "everybody's site" — which is how a wayback query for `{host}/*` enumerates strangers'
    captures, and how any host check accepts a stranger's profile on the same platform.
    """
    host = (host or "").lower().rstrip(".").removeprefix("www.")
    if not host:
        return False
    return any(host == shared or host.endswith(f".{shared}") for shared in SHARED_HOSTS)


def on_own_host(url: str, person: PersonRef) -> bool:
    """True when `url` sits inside the WEB SPACE the roster gave for this person.

    The strongest identifier this system has short of a QID, and the only one that needs
    no corroboration: the club wrote the address down next to the member's name.

    "Web space", not "domain", is the whole subtlety.  On a domain the member owns, the
    roster's URL vouches for every path.  On a shared platform it vouches for exactly the
    path it names and its descendants — `linkedin.com/in/marisol-quennebeck/*` is hers,
    `linkedin.com/in/anybody-else` is a stranger, and they differ only below the host.
    """
    if not url:
        return False
    target = urlsplit(url)
    host = (target.hostname or "").lower()
    if not host:
        return False
    for declared in urls_in(person.details):
        source = urlsplit(declared)
        if (source.hostname or "").lower() != host:
            continue
        if not is_shared_host(host):
            return True
        prefix = source.path.rstrip("/")
        if not prefix:
            # The roster named a shared platform's ROOT and nothing more. That names no
            # one, so it vouches for no one.
            continue
        if target.path == prefix or target.path.startswith(prefix + "/"):
            return True
    return False


def corroboration(haystack: str, terms: Sequence[str]) -> int:
    """How many of `terms` this text independently echoes."""
    hay = normalize_ws(haystack)
    return sum(1 for term in terms if term and term in hay)


def corroborates(person: PersonRef, *parts: object) -> int:
    """`corroboration` against everything the roster supplied about `person`."""
    joined = " ".join(str(part) for part in parts if part is not None)
    return corroboration(joined, roster_terms(person))


# -- the contract ------------------------------------------------------------------------


def identifies(
    person: PersonRef,
    *,
    names: Iterable[object] = (),
    prose: Iterable[object] = (),
    urls: Iterable[object] = (),
    context: Iterable[object] = (),
) -> bool:
    """Does this item carry evidence that it is about `person`? The shared oracle.

    Pass what the source gave you, sorted by what KIND of field it is:

    * ``names``   — fields whose job is to name one entity (a profile `name`, an EDGAR
      `display_names` entry, a Wikidata label, an HN `author` handle). Tested with
      `carries_name`.
    * ``prose``   — free text: titles, snippets, abstracts, bios, archived page bodies.
      Tested with `mentions_name`, which additionally requires the words to be adjacent.
    * ``urls``    — where the item lives, and where it points. An item on the member's own
      domain is accepted outright.
    * ``context`` — anything else that might echo a roster detail: a company field, a
      location, a repository description, an institution.

    Three ways to be yes, and there is no fourth:

    1. the item is ON one of the member's own hosts; or
    2. a ``names`` field names them **and** something in the item echoes a roster detail;
    3. the ``prose`` names them in full **and** something in the item echoes a detail.

    A name with nothing behind it is **no** — that is the entire point of the module. So
    is a person the roster describes with nothing but a name: with no host and no
    affiliation there is nothing to corroborate against, every rule fails closed, and the
    honest output is no documents rather than a stranger's.
    """
    url_list = [str(url) for url in urls if url]
    if any(on_own_host(url, person) for url in url_list):
        return True

    name_list = [str(value) for value in names if value]
    prose_list = [str(value) for value in prose if value]
    named = any(carries_name(value, person.name) for value in name_list) or any(
        mentions_name(value, person.name) for value in prose_list
    )
    if not named:
        return False

    return (
        corroborates(person, *name_list, *prose_list, *url_list, *context) > 0
    )


def choose_one[T](
    candidates: Sequence[T],
    score: Callable[[T], int],
    *,
    require_corroboration: bool = False,
) -> T | None:
    """The one candidate the roster's own details single out, or `None`. Ties decline.

    Hoisted from the OpenAlex connector, which is where the rule was first written down:
    once more than one profile publishes under a name, **the name has stopped being an
    identifier**, only a detail the roster supplied can break the tie, and a tie that
    stays tied is answered with nothing rather than with the first result.

    `require_corroboration` makes a lone candidate prove itself too. Off by default,
    because that is OpenAlex's shipped behaviour and this hoist must not change it; on for
    `self_page`, which stamps the highest-trust `SourceKind` in the system and has no
    business believing a search index that returned exactly one row.
    """
    if not candidates:
        return None
    if len(candidates) == 1 and not require_corroboration:
        return candidates[0]
    ranked = sorted(
        ((score(candidate), index) for index, candidate in enumerate(candidates)),
        key=lambda pair: (-pair[0], pair[1]),
    )
    best = ranked[0]
    if best[0] <= 0:
        return None
    if len(ranked) > 1 and ranked[1][0] == best[0]:
        return None
    return candidates[best[1]]
