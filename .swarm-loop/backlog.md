# Backlog — Arrival Engine

**The authoritative ticket graph is `tickets.json` at the repo root.** This file is its
human view and is REGENERATED from it — never hand-edited. Last regenerated after
freeze amendment #1 (ESC-005), 2026-09-03.

- Tickets in graph: **27**
- Closed: **6**
- Open: **21**

Original ten came from the build-docs task graph. T-010 onward were minted by
`swarmloop.py findings` from adversarial-verifier and lane self-reports; each carries
a `verify` PLACEHOLDER that `red-check` refuses, so a gate must be written before any
of them dispatches.

---

## Closed

- **T-0** — Repo skeleton, contracts, test harness and fixtures exist and the harness discriminates by ticket
- **T-1** — Every connector returns cited RawDocs from recorded fixtures, and the HTTP core is rate-limited, cached and ne
- **T-2** — The resolver accepts the target, rejects same-name decoys, and the real LLM client conforms to the double
- **T-3** — The extractor emits schema-valid facts and hubs, and drops any fact whose quote is not in its source
- **T-4** — The taste filter excludes every must-exclude case and keeps every must-keep case in the owner-approved fixture
- **T-5** — Matching ranks rare shared hubs above generic ones and exposes the score components and path

## Open — original feature tickets

- **T-6** — `python -m arrival build` produces validated dossiers and a report from a roster, fully offline agai
  - depends on: T-1, T-2, T-3, T-4
- **T-7** — The digest builder enforces every cap, cites every fact, and always yields a say-out-loud line withi
  - depends on: T-4, T-5
- **T-8** — The web app boots from dossiers, tracks presence, and renders digest, building, debug and demo-drive
  - depends on: T-5, T-7
- **T-9** — Ship artifacts: render.yaml, committed-dossier validator, README with hours log and next-month parag
  - depends on: T-6, T-8

## Open — minted fix tickets (verify placeholders, need a gate before dispatch)

- **T-010** — hub_id is not stable across dossiers, so two people who genuinely share a rare hub can score 0. Three ways the
- **T-011** — T-3 acceptance 3 says 'the same label across two docs yields ONE Hub with merged evidence_fact_ids'. It does n
- **T-012** — _document_block can be replaced by `return ""` — sending the LLM ZERO document text — and all 10 frozen T-3 cr
- **T-013** — Match.path is order-dependent: 360 of 720 dossier permutations produce a different path for the same input. Sc
- **T-014** — _merge_groups' cross-id merge branch (existing.add_evidence / existing.recency = max) is executed by ZERO test
- **T-015** — Three holes in the citation guard, which is the hallucination guard the whole product's trustworthiness rests 
- **T-016** — Match.path can name a hub that Match.why denies, and it reproduces on the frozen corpus itself. _why names onl
- **T-017** — The Wikidata connector does not filter candidates by person.details, and it is the only name-searching connect
- **T-018** — The person's CITY is used as a nonprofit-name query, and there is NO filter requiring the person to appear in 
- **T-019** — No author disambiguation: the first search result carrying a display_name wins. Another researcher who shares 
- **T-020** — No relevance filter, so a page about the member's COMPANY is emitted as a document about the PERSON — measured
- **T-021** — Acceptance 2 requires the self_page connector to fetch '/feed' RSS if present. It is implemented BACKWARDS: /f
- **T-022** — The /events endpoint is entirely absent — grep events returns zero hits — while acceptance 2 names 'recent pub
- **T-023** — FORMS is '3,4,5,D' against the ticket's stated 'D/4/13F'. The docstring argues 13F is holdings data and theref
- **T-024** — No author search and no comment retrieval; the ticket says 'Algolia by author/name'. Only story search by name
- **T-025** — There is NO TTL anywhere in the HTTP cache. An empty or unextractable 200 is cached permanently: fetched_at is
- **T-026** — A response with no Content-Type defaults to text/html, and the HTML extractor then strips angle brackets from 

## Scheduling constraints

- `src/arrival/extract.py` is touched by T-010, T-011, T-012, T-014, T-015 — ONE lane, not five.
- `src/arrival/graph.py` is touched by T-013 and T-016 — ONE lane.
- T-6 and T-7 consume `extract` and `graph`; do not run an extract/graph fix lane
  concurrently with them. An empty FILE intersection is not a clearance when the
  fix changes hub-id VALUES the consumer's fixtures depend on.
- `HOURS.md` is orchestrator-owned and in no ticket's scope.
