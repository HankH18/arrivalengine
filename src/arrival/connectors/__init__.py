"""The fan-out: `all_connectors(settings) -> list[Connector]` (DESIGN fn-table).

TEN connectors, and the list is exact in both directions.

**No fewer**, because "go wide" is the strategy: no single free source knows enough about a
private-club member to build a dossier, and the ones that do know something each know a
different thing — Wikidata knows who they *are*, GitHub knows what they *build*, ProPublica
knows whose *board* they sit on. A partial fan-out does not degrade gracefully; it silently
produces a thinner dossier that looks exactly like a complete one.

**No more**, because two of the obvious candidates are deliberately withheld.  SPEC Q4
leaves `fec` (political donations) and `courtlistener` (court records) unbuilt, and R11
forbids ever displaying them.  They are not omitted for lack of time — they are omitted
because this is a hospitality product, and a host who greets a member with their donation
history has crossed the line the whole thing is scored on.  A source that can never be
shown is a source there is no reason to have fetched, so the withholding happens *here*, at
the fan-out, rather than downstream in the taste filter where the data would already exist.
`uspto`, `youtube` and `podcast` are likewise out of scope for this ticket.

ORDER IS DISPLAY PRIORITY, as DESIGN's function table specifies: most trustworthy and most
attributable first.  The person's own page outranks an encyclopedia entry about them, which
outranks a public record naming them, which outranks a search engine's guess.  It is not
the order the sources are *queried* in — T-6 fans out concurrently — it is the order a
reader should meet them in.
"""

from __future__ import annotations

from arrival.config import Settings
from arrival.connectors.base import BaseConnector
from arrival.connectors.edgar import EdgarConnector
from arrival.connectors.github import GithubConnector
from arrival.connectors.hn import HackerNewsConnector
from arrival.connectors.openalex import OpenAlexConnector
from arrival.connectors.propublica import ProPublicaConnector
from arrival.connectors.search import SearchConnector
from arrival.connectors.self_page import SelfPageConnector
from arrival.connectors.wayback import WaybackConnector
from arrival.connectors.wikidata import WikidataConnector
from arrival.connectors.wikipedia import WikipediaConnector
from arrival.contracts import Connector, SourceKind

__all__ = [
    "CONNECTOR_CLASSES",
    "DISPLAY_PRIORITY",
    "WITHHELD_KINDS",
    "BaseConnector",
    "EdgarConnector",
    "GithubConnector",
    "HackerNewsConnector",
    "OpenAlexConnector",
    "ProPublicaConnector",
    "SearchConnector",
    "SelfPageConnector",
    "WaybackConnector",
    "WikidataConnector",
    "WikipediaConnector",
    "all_connectors",
]

#: Source classes SPEC Q4/R11 keep out of the product. Listed rather than merely absent, so
#: the exclusion is a decision a reader can find instead of an omission they must notice.
WITHHELD_KINDS: frozenset[str] = frozenset({"fec", "courtlistener"})

#: kind -> implementation, in DISPLAY PRIORITY order (see the module docstring). Python
#: dicts preserve insertion order, so this literal IS the ordering.
CONNECTOR_CLASSES: dict[SourceKind, type[BaseConnector]] = {
    "self_page": SelfPageConnector,  # their own words, on their own domain
    "wikipedia": WikipediaConnector,  # edited, sourced, and about them by name
    "wikidata": WikidataConnector,  # the identifier spine every hub_id keys on
    "github": GithubConnector,  # published work, attributed to a login they chose
    "openalex": OpenAlexConnector,  # authored papers and the co-authors on them
    "edgar": EdgarConnector,  # public record: roles and affiliations
    "propublica": ProPublicaConnector,  # public record: boards and causes
    "hn": HackerNewsConnector,  # their own posts, in a community context
    "wayback": WaybackConnector,  # what their site used to say
    "search": SearchConnector,  # the open web's guess, least attributable
}

#: The kinds `all_connectors` returns, in order. A tuple so a caller cannot rearrange it.
DISPLAY_PRIORITY: tuple[SourceKind, ...] = tuple(CONNECTOR_CLASSES)


def all_connectors(settings: Settings | None = None) -> list[Connector]:
    """Every connector this build fans out over, in display-priority order.

    `settings` is passed to each connector rather than fetched by it, so one process can
    build with two configurations (a test corpus and a live one) without a global. `None`
    means "read `get_settings()` when you need it", which is what the CLI wants.
    """
    return [connector_class(settings) for connector_class in CONNECTOR_CLASSES.values()]
