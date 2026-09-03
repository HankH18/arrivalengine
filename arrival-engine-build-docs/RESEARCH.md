# Arrival Engine — Research Summary (reference; not loaded by agents)

Condensed from the deep-research pass of 3 Sep 2026. Full findings drove SPEC/DESIGN; this file exists so the reasoning behind source choices survives in the repo and can be quoted on Friday under "data judgment".

## Free-search landscape (Sept 2026)
- Brave Search API: no-card free tier removed Feb 2026 — avoid.
- Google Programmable Search JSON API: closed to new customers (Jan 2026), sunsets 1 Jan 2027 — avoid.
- Bing Search APIs: retired 11 Aug 2025 (HTTP 410) — gone.
- Tavily: 1,000 credits/month, no card, returns extracted page text — primary. (Re-verify tier at signup.)
- Exa: small monthly free credits, no card — optional secondary.
- DuckDuckGo lite/html: unofficial, CAPTCHA/IP blocks under load — low-volume fallback only.
- SearXNG self-hosted: free to run, gets CAPTCHA'd from one IP — not worth the time in a 14 h build.

## Open sources that score on "creative sourcing"
| Source | Access | Limits | Why it matters |
|---|---|---|---|
| SEC EDGAR full-text + submissions | free, no key, real User-Agent required | 10 req/s | Form D fundraises, Form 4 insider, 13F — pre-press signal |
| Wikidata SPARQL / MediaWiki API | free, no key | ~60 s query time/min/IP, 60 s timeout; slow in 2026 — cache | Canonical QIDs for people and affiliations (anchor for resolution and hubs) |
| Wayback CDX | free, no key | ~1 req/s norm; use `collapse=digest` | Old About pages, deleted projects — "not on the first page" |
| GitHub REST/GraphQL | free | 5,000/hr with token | What they are building now |
| ProPublica Nonprofit Explorer (990s) | free, no key | be polite | Board seats and causes — "what they care about" |
| HN Algolia | free, no key | ~10k/hr/IP | Their own comments / Show HN |
| OpenAlex / Semantic Scholar / arXiv | free, no key (arXiv: 1 req/3 s) | — | Use instead of Google Scholar (scraping gets 429/CAPTCHA) |
| USPTO Open Data Portal | free key, ~45/min | PatentsView migrated to ODP 20 Mar 2026 | Patents — low yield for this roster; not built by default |
| Podcast Index | free, open | generous | Prefer over Listen Notes (free tier forbids server-side caching) |
| YouTube Data API | free | 10,000 units/day (~100 searches) | Talks/transcripts; not built by default |
| FEC / OpenFEC | free api.data.gov key | 1,000/hr | Political donations — capability only; never displayed (taste) |
| CourtListener | free token | defaults lowered May 2026 | Court records — capability only; never displayed (taste) |
| Texas SOS SOSDirect | $1/search, login | — | Skip; not free |

Excluded from display on taste grounds even though public: home/property, family, health, litigation/court, wealth/comp figures, political donations.

## Architecture choices and why
- Orchestrator-worker research (Anthropic's multi-agent research pattern): parallel subagents per source class, hard budgets, separate citation pass. Anthropic's own eval reported ~90% improvement over single-agent but ~15× token use — hence pre-compute offline, never on the arrival path.
- Entity resolution: Wikidata/strong-key anchor + per-document LLM verdict with verbatim evidence + single-contradiction veto ("molecular facts" approach: pick one disambiguating fact and apply it consistently).
- Matching: bipartite knowledge graph (people = leaves, entities = hubs) with IDF-style hub weights — the standard TF-IDF edge-weighting for user–object bipartite networks — and a weighted shortest path as the explanation (path-based explainability, cf. KPRN-style path reasoning).
- Hospitality norm: "recognized, not surveilled." Use only professional, self-published or public-record material the person would be glad to be asked about; show provenance; publish the exclusion policy in the product.

## Hosting
- Render free web service: no card, Git deploy, outbound allowed, spins down after 15 min idle (30–60 s cold start) — warm it before the demo. Ephemeral disk → commit dossiers.
- Cloudflare Tunnel from a laptop: fallback with no cold start.
- Avoid Fly.io (card required, trial only), Koyeb (no standing free compute), Vercel (serverless timeouts kill scraping).

## Demo (10 min) and likely questions
Arrive → digest in <2 s → read a Meet "why" aloud → open reasoning → show a Form D / 990 / Wayback find → open `/debug` and show a withheld family/address fact with its reason → hours spent → next-month paragraph.
Expect: how did you pick what to leave out; most creative source; hours; how do you know it's the right person; what breaks at 200 members; show me a false match.
Ask them: what does a host do in the 90 s; worse outcome — missed intro or creepy one; how fresh is "lately"; would members want to see their own dossier.
