# Arrival Engine — Spec

Build classification: **greenfield**, mixed bounded/exploratory, **small build** (10 tickets → single `TASKS.md`).
Priority order (from the owner): **correctness > speed > everything else**. Budget: ~14 working hours, due Fri 4 Sep 2026 09:45 CT.

## Problem & intent

Arena Hall (a private club for Texas founders and investors) wants a staff-facing engine: when a member arrives (a webhook fires with a name plus one or two identifying details), staff instantly get a tight digest — who walked in, who else in the building they should meet and *why*, and a short dossier with a conversation opener. It must feel like hospitality, not surveillance. It is scored on speed-to-working, data judgment (creative open sourcing), signal over noise, and taste (the "seen vs. dossiered" line). Ten public figures stand in for members; they are listed in `data/roster.yaml` and are researched only by the T-9 build run — never used in test fixtures.

## User-visible behavior

Actors: **Host** (club staff reading the digest), **Operator** (developer running builds), **Webhook** (arrival detection, assumed solved).

### Roster and pre-computation
- R1. WHEN the operator runs the build command against a roster file listing people as `{name, details[]}`, THE SYSTEM SHALL produce one dossier per person, persisted to disk, and report per-person resolution confidence and fact counts.
- R2. WHEN a person cannot be resolved with at least one strong identifier OR two independent corroborating attributes, THE SYSTEM SHALL mark the dossier `unresolved`, store no facts for them, and say so in the build report rather than guess.

### Arrival and presence
- R3. WHEN `POST /arrive` is received with `{name, details?}`, THE SYSTEM SHALL match the name to a roster person, add them to the in-memory presence set, compute matches against everyone else currently present, and return a digest id — in under 3 seconds when a cached dossier exists.
- R4. WHEN `POST /arrive` names someone not on the roster, THE SYSTEM SHALL respond 404 with a message; it SHALL NOT trigger live research.
- R5. WHEN `POST /leave` is received for a present person, THE SYSTEM SHALL remove them from presence; subsequent digests SHALL NOT propose them as a match.
- R6. `GET /building` SHALL list who is present now.

### Digest
- R7. `GET /digest/{id}` SHALL render an HTML page containing exactly these sections, in order, with hard caps: **Who** (1 line: name, current role/company, what they are working on now); **Meet** (≤3 present people, each with a 0–100 score and a one-sentence "why" that names the shared thing); **Lately** (≤3 bullets, most recent professional activity first); **Not on the first page** (exactly 1 fact when one is available, from a non-search source or a Wayback/archival find; otherwise the section says none was found); **Say out loud** (exactly 1 opener the person would enjoy being asked); **Why we know this** (numbered source list with URL and retrieval date for every fact shown).
- R8. WHEN nobody else is present, the Meet section SHALL say so explicitly rather than pad.
- R9. Every fact shown in a digest SHALL carry a citation to a source URL and a verbatim supporting quote, and the citation SHALL be visible from the digest page.
- R10. Each Meet item SHALL expose, on the digest page (behind a "show reasoning" toggle or inline), the score components: the shared hubs, each hub's weight, and the recency/type multipliers.

### Taste line (the product dies on the wrong side of this)
- R11. THE SYSTEM SHALL NEVER display, in any host-facing view: home/property addresses or property records; family members, relationships, children; health/medical information; litigation, criminal, divorce or court records; net worth, compensation or wealth figures; political donations or affiliations. Facts in these categories are retained internally as `excluded` with a reason and are viewable only on an operator-only `/debug` view.
- R12. THE SYSTEM SHALL only display facts whose provenance `source_kind` is on the display whitelist pinned in DESIGN §Data models (self-authored pages, press via search, public records, code/talk/podcast platforms), whose `confidence ≥ 0.7`, and whose category is not a taste-excluded one; the source URL is shown so the host can open it.
- R13. The digest page SHALL display a one-paragraph exclusion policy stating what the system will never surface.
- R14. The "Say out loud" line SHALL be phrased as an invitation about something the person has publicly done or said, never as a statement revealing what the system knows ("Ask about…", not "I saw that you…").

- R18. (Verbatim from the client: "Treat these ten as stand-ins for real members. Write your scoring logic and your tone as though a host will read the output aloud to a founder standing in our lobby, with about ninety seconds to do it.") The `who_line`, every Meet `why`, and the `say_out_loud` line SHALL be speakable as written: plain sentences, no URLs, no numbers-as-scores, no parentheticals or citation markers inside the sentence; citations live only in the "Why we know this" list.

### Operator surfaces
- R15. `GET /debug/{person_id}` SHALL show the full dossier including excluded facts (with reasons), rejected candidate documents, per-fact confidence, and the raw hub list — the "we know where the line is" demo view. It is served only when env `DEBUG_VIEWS=1`; otherwise 404. (This is a switch, not auth — see non-goals.)
- R16. The README SHALL contain an hours log (what was done, hours spent, per session) and the deploy URL.

### Optional (do only if T-1..T-9 are closed with time remaining)
- R17. The interest graph (people as leaves, entities/interests as hubs) SHALL be viewable as a simple rendered graph on `/graph` showing present people and their shared hubs.

## Constraints

- C1. Free/open sources and free-tier tooling only; **no service that requires a payment card**. Approved: Tavily (no-card tier), DuckDuckGo lite (fallback), Wikidata/Wikipedia, SEC EDGAR, USPTO ODP, ProPublica Nonprofit Explorer, Wayback CDX, GitHub API, HN Algolia, OpenAlex/arXiv, YouTube Data API, Podcast Index, direct fetch of public pages/RSS. Excluded from *display* but permitted as capability if time allows: FEC, CourtListener.
- C2. No logged-in scraping of any site. No LinkedIn or X scraping beyond fetching a public URL that responds to an anonymous request; skip on failure.
- C3. Stack: Python 3.12, FastAPI, httpx, Pydantic v2, NetworkX, Jinja2, Anthropic SDK; pytest + ruff. No database server; dossiers are JSON files on disk committed to the repo.
- C4. Hosting: Render free web service (primary), Cloudflare Tunnel from a laptop (fallback). App must boot from committed JSON dossiers with no build-time network access.
- C5. All outbound HTTP declares a User-Agent with a contact email and respects per-host rate limits (SEC ≤10/s, Wikidata ≤ a few concurrent, arXiv ≤1/3s, USPTO ≤45/min, Wayback ≈1/s).
- C6. LLM calls use temperature 0, JSON-schema structured output, and prompt caching for the stable prefix. Pre-computation may use the Batch API; the arrival path may not (latency).
- C7. Tests run offline: every network-dependent unit is tested against recorded fixtures and an `LLMDouble`; no test may hit the network.
- C8. Correctness beats coverage: a fact with no verbatim quote in its source text is dropped, not shown.

## Non-goals

- No facial recognition, camera, or arrival detection of any kind.
- No login, accounts, auth, roles, or multi-tenant anything.
- No database server, queue, or background-worker infrastructure.
- No paid data or enrichment APIs (Apollo, Clearbit, PDL, Crunchbase API, Listen Notes paid).
- No live "go wide" research on the arrival path — arrival reads cached dossiers and at most runs a fast refresh of GitHub/news (and only if T-6 ships it).
- No polish: no design system, no JS framework, no mobile layout. Server-rendered HTML.
- No research on the ten real subjects except the T-9 build run; all test fixtures use synthetic people.
- No sentiment, personality, or psychological inference about anyone.

## Success criteria

- S1. `python -m arrival build --roster tests/fixtures/roster_synthetic.yaml` (two synthetic people, doubles injected) produces two validated dossier JSON files and a report; verified by T-6's tests.
- S2. With three people present, `POST /arrive` for a fourth returns in <3s and the digest renders all R7 sections with caps enforced; verified by T-8's tests using fixture dossiers.
- S3. Given the human-approved taste fixture set (`tests/fixtures/taste_cases.yaml`), the taste filter excludes 100% of the must-exclude cases and passes 100% of the must-keep cases; verified by T-4.
- S4. Given a document set containing a same-name decoy, the resolver rejects the decoy and accepts the target; verified by T-2.
- S5. Given fixture dossiers where two people share only generic hubs ("Austin", "AI") and two others share a rare hub, the rare-hub pair outranks the generic pair; verified by T-5.
- S6. Every fact in a rendered digest has a provenance entry whose quote is a substring of the stored source text; verified by T-3 (citation check), T-7 (digest sources), and T-8 (render).
- S7. The app boots on Render from committed dossiers and serves `/building` within the free tier; verified manually in T-9 and recorded in the README.
- S8. README hours log is present and the deploy URL is live at submission.

## Open questions (resolved by default; override before T-1 starts)

Each was a hidden assumption in the plan. The default is what the docs assume.

- Q1. Roster identity keys: `person_id = slug(name)`. The ten (in `data/roster.yaml`) have no duplicate names. **Resolved.** Note the ten are not Texas-based (NY, Boulder, Philadelphia, SF, Sydney), so city is a resolver detail, not a shared hub. One known same-name decoy: Nabeel Qureshi (writer/researcher, ex-Palantir) vs. the late Nabeel Qureshi (author/apologist, d. 2017) — the resolver must reject the latter; this is a required resolve_case in T-2.
- Q2. Search provider: default Tavily (free, no card, 1,000 credits/month). If the operator does not want to create a Tavily account, the system falls back to DuckDuckGo lite only and coverage drops. **Default: create the Tavily account.**
- Q3. Fast refresh on arrival (GitHub + news): included in T-6 as optional; first thing cut if behind. **Default: build it last inside T-6.**
- Q4. FEC and CourtListener connectors: excluded from display by R11 regardless. Building them only serves the debug demo of "we found it and withheld it". USPTO, YouTube and Podcast Index connectors are also not built by default — low yield for a roster of investors and writers, and each costs ~20 min. **Default: not built; the demo uses a Wayback/990 find that is *kept* and an address/family fact that is *withheld*, which is a cheaper way to show the same judgment.**
- Q5. "Say out loud" generation uses one LLM call at arrival time with a cached prefix. **Default accepted; fallback is the highest-confidence hook fact phrased by template.**
