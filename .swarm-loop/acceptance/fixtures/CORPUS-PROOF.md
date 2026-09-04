# CORPUS-PROOF - verification of the frozen dossier and RawDoc corpus

Audit trail for **frozen-spec sections 1, 1a, 1b, 1c and 2**. Everything below was
computed by scripts that load the JSON files **exactly as committed** in this
directory. No number in the frozen spec was taken on trust; each is a hypothesis the
scripts either confirm or contradict, and the contradictions are listed in
*Disagreements with the spec* at the bottom rather than papered over.

| | |
|---|---|
| Dossiers (resolved, in the graph) | 5 - `fixtures/dossiers/*.json` |
| Dossier (unresolved, NOT in the graph) | 1 - `fixtures/dossiers_unresolved/vex-tarrow.json` |
| RawDocs | 23 - `fixtures/docs/<doc_id>.json` |
| Facts | 35 |
| Verification scripts | 7, all exit 0 |
| Named assertions | 82 (12 in check 1, 15 in check 4, 41 in check 6, 14 in check 7) - all PASS |
| Exhaustive sweeps | 38 quote checks, 23 doc_id/sha1 checks, 40 real-name/entity probes, 16 hub-id canonicalisations, 70 provenance-vs-RawDoc field comparisons - 0 failures |

All seven scripts live in the authoring agent's scratch space, **not** in the repo, and
are reproduced verbatim below so a reader can re-run them against the committed files.

> **Revision - hub id corrected.** `topic:developer-tools-gtm` was changed to
> `topic:developer-tools-go-to-market` in `runa-okonkwo.json` and `jem-arrowood.json`,
> because the old id was not `{type}:{slug(label)}` and so was a hub no correct extractor
> could emit (see *Disagreements* section 1, now resolved). **Every one of the seven checks
> was re-run against the edited corpus, all seven exit 0, and every "Verbatim output" block
> below is the output of that re-run.** Checks 2-6 reproduced their previous output byte
> for byte. Check 1 was diffed against a run over a *reconstruction of the pre-fix corpus*
> under the same interpreter: it changes on exactly three lines, all three the hub-id
> string, with every number identical. Check 7 is new and exists to make this class of
> defect impossible to reintroduce silently. **No graded number moved.**
>
> Interpreter matters for one line of check 1: it prints the version of whatever `networkx`
> the running interpreter has (`networkx 2.6.3 cross-check:` here). Diff check 1's output
> against another interpreter's and that line will differ without any corpus having
> changed - so re-run the before/after pair under the SAME `python3`.

---

## Hub table - carriers, IDF, type boost

`N = 5` (person nodes in `fixtures/dossiers/`; the unresolved dossier is in a separate
directory precisely so it cannot perturb `N`).

`idf(hub) = max(0, ln(N / (1 + n_people_on_hub)))`

The denominator is **smoothed**: `1 + n`, not `n`. Read the `ln(5/3)` entries below as the
reduced form of `ln(5 / (1 + 2))` for a hub carried by **two** people - not as `ln(N/n)`
with three carriers. Two hubs here are carried by 2 people and two by 1; with the
unsmoothed form every one of those numbers would be wrong, and `n=5` would be `ln(1)=0` by
coincidence rather than `ln(5/6)` clamped to 0.

| hub_id | label | type | n | carriers | ln(N/(1+n)) | idf | type_boost | note |
|---|---|---|---|---|---|---|---|---|
| `city:austin` | Austin | `city` | 5 | jem-arrowood, mira-hollowell, runa-okonkwo, sil-vantorre, theo-baptiste | `ln(5/6) = -0.182322` | **0.000000** | 0.5 | clamped to 0 |
| `company:lantern-freight` | Lantern Freight | `company` | 1 | mira-hollowell | `ln(5/2) = 0.916291` | **0.916291** | 1.5 | - |
| `investor:foundry-seed-2019` | Foundry Seed 2019 | `investor` | 2 | runa-okonkwo, sil-vantorre | `ln(5/3) = 0.510826` | **0.510826** | 1.5 | - |
| `school:bellhaven-polytechnic` | Bellhaven Polytechnic | `school` | 1 | theo-baptiste | `ln(5/2) = 0.916291` | **0.916291** | 0.8 | - |
| `topic:developer-tools-go-to-market` | Developer-tools go-to-market | `topic` | 2 | jem-arrowood, runa-okonkwo | `ln(5/3) = 0.510826` | **0.510826** | 1.0 | - |
| `topic:remote-work` | Remote work | `topic` | 5 | jem-arrowood, mira-hollowell, runa-okonkwo, sil-vantorre, theo-baptiste | `ln(5/6) = -0.182322` | **0.000000** | 1.0 | clamped to 0 |

`REF = ln(N/3) * 1.5 = ln(5/3) * 1.5 = 0.766238`

The `3` in `REF` is the same `1 + n` with `n = 2`: DESIGN Decision 3 normalises against
"one rare hub shared by exactly two people, with the highest type boost and full
recency". That is why `investor:foundry-seed-2019` scores exactly 100 - its raw score IS
the reference - and why the topic hub, identical in membership but boosted 1.0 instead of
1.5, scores `round(100 * 0.510826 / 0.766238) = 67`.

Derived scores, all recomputed in check 1:

| pair | shared hubs that contribute | raw | score |
|---|---|---|---|
| `runa` x `sil` | `investor:foundry-seed-2019` (0.510826 x 1.0 x 1.5) | 0.766238 | **100** |
| `runa` x `jem` | `topic:developer-tools-go-to-market` (0.510826 x 1.0 x 1.0) | 0.510826 | **67** |
| `runa` x `mira` | none (both clamped hubs contribute 0) | 0.000000 | **0** |
| `runa` x `theo` | none (both clamped hubs contribute 0) | 0.000000 | **0** |
| `mira` x `theo` | none (both clamped hubs contribute 0) | 0.000000 | **0** |

Ranking asserted by the T-5 frozen test: **sil (100) > jem (67) > {mira, theo} (0)**.
`sil`'s top contribution hub is `investor:foundry-seed-2019` and the weighted shortest
path (edge cost `1/(1+idf)`) is
`['person:runa-okonkwo', 'hub:investor:foundry-seed-2019', 'person:sil-vantorre']`
at cost 1.323780, beating the two clamped-hub routes at cost 2.000000. Confirmed twice:
by a hand-written heapq Dijkstra and, independently, by `networkx 2.6.3`.

**Unique hubs never contribute.** `company:lantern-freight` (mira) and
`school:bellhaven-polytechnic` (theo) each carry a *high* idf of 0.916291 - and still
contribute nothing to any pair, because contribution requires an overlap. That is the
point of including them: an implementation that summed hub weights per person instead
of per shared hub would light these up and score `mira x theo` non-zero.

---

## RawDoc corpus

`doc_id = sha1(url.encode()).hexdigest()[:16]`, verified for every file in check 3.

| doc_id | source_kind | published_at | chars | url |
|---|---|---|---|---|
| `137675365d8ea470` | `search` | 2026-01-22 | 605 | https://example.org/tradepress/2026/tallow-harbor-trial-rewrite |
| `22b557df95a72095` | `github` | 2024-08-05 | 269 | https://example.com/github/vextarrow |
| `31173fc736e73821` | `fec` | 2022-01-31 | 589 | https://example.org/fec/filings/C00-4471902 |
| `35b4e2600c8a6ea6` | `self_page` | 2026-01-05 | 930 | https://example.com/runa-okonkwo/about |
| `4583e496f241803c` | `search` | 2023-02-24 | 370 | https://example.org/newswire/2023/tarrow-one-room-gallery |
| `4fa82f5ab51b8b02` | `podcast` | 2025-10-14 | 798 | https://example.org/podcasts/harbor-lines/episode-88 |
| `50957dd279c64c59` | `self_page` | 2026-01-19 | 697 | https://example.com/jem-arrowood/now |
| `5d952930adb32fe7` | `hn` | 2025-08-19 | 645 | https://example.org/hn/item?id=41220885 |
| `64285175617dde55` | `openalex` | 2025-11-04 | 610 | https://example.org/openalex/works/W2201194 |
| `8aca032fc32f4221` | `search` | 2025-06-18 | 616 | https://example.org/tradepress/2025/bellhaven-sensor-audit |
| `8e95f057ad101a20` | `self_page` | 2026-01-12 | 735 | https://example.com/sil-vantorre |
| `9011fe302dab10ba` | `github` | 2025-05-27 | 678 | https://example.com/github/quarrystone/cli |
| `92b1d32390d8795f` | `search` | 2026-02-11 | 759 | https://example.org/tradepress/2026/quarrystone-platform-roadmap |
| `b46212cb0a5a8c0c` | `search` | 2025-09-30 | 598 | https://example.org/tradepress/2025/lantern-freight-dispatch-rules |
| `babaa3f0a06e9dfe` | `search` | 2024-10-02 | 668 | https://example.org/bouldin-ledger/2024/quarterly-renovation-permits |
| `bd95ab53aac6c458` | `self_page` | 2025-10-01 | 634 | https://example.com/bellhaven-polytechnic/people/theo-baptiste |
| `ca6310f83f9a9387` | `search` | 2024-04-16 | 743 | https://example.org/city-monthly/2024/the-dispatcher |
| `cf0c86082dfcc081` | `search` | 2025-07-21 | 652 | https://example.org/harbor-notes/2025/ferry-timetable-thread |
| `d07ba24408f2aa13` | `self_page` | 2025-11-20 | 677 | https://example.com/lantern-freight/team/mira-hollowell |
| `d3541f5a1b10b96a` | `search` | 2025-12-09 | 660 | https://example.org/tradepress/2025/foundry-seed-vintage-review |
| `d9902fb9cd225788` | `wayback` | 2017-06-14 | 649 | https://web.example.org/web/20170614/quarrystonelabs.example.com/status |
| `de86db5a839147e2` | `search` | 2025-03-08 | 688 | https://example.org/city-monthly/2025/the-platform-builder |
| `e4ba96415536ce5f` | `search` | 2019-09-12 | 483 | https://example.org/newswire/2019/okonkwo-named-deputy-harbourmaster |

Three docs are deliberately **not** cited by any Fact: they are the rejected-candidate
evidence behind `resolution.rejected` (one same-name decoy for `runa`, two ambiguous
candidates for the unresolved `vex-tarrow`), so `/debug` has real material to show.

---

## Fact inventory

| fact_id | person | category | source_kind | conf | display status | doc_id |
|---|---|---|---|---|---|---|
| `jem-arrowood-f01` | jem-arrowood | `current_work` | `self_page` | 0.91 | displayable | `50957dd279c64c59` |
| `jem-arrowood-f02` | jem-arrowood | `affiliation` | `self_page` | 0.86 | displayable | `50957dd279c64c59` |
| `jem-arrowood-f03` | jem-arrowood | `interest` | `self_page` | 0.83 | displayable | `50957dd279c64c59` |
| `jem-arrowood-f04` | jem-arrowood | `recent_activity` | `search` | 0.87 | displayable | `137675365d8ea470` |
| `mira-hollowell-f01` | mira-hollowell | `current_work` | `self_page` | 0.9 | displayable | `d07ba24408f2aa13` |
| `mira-hollowell-f02` | mira-hollowell | `affiliation` | `search` | 0.85 | displayable | `b46212cb0a5a8c0c` |
| `mira-hollowell-f03` | mira-hollowell | `interest` | `self_page` | 0.82 | displayable | `d07ba24408f2aa13` |
| `mira-hollowell-f04` | mira-hollowell | `recent_activity` | `search` | 0.84 | displayable | `b46212cb0a5a8c0c` |
| `mira-hollowell-f05` | mira-hollowell | `interest` | `search` | 0.86 | **EXCLUDED health** | `ca6310f83f9a9387` |
| `mira-hollowell-f06` | mira-hollowell | `interest` | `search` | 0.84 | **EXCLUDED wealth** | `ca6310f83f9a9387` |
| `runa-okonkwo-f01` | runa-okonkwo | `current_work` | `self_page` | 0.93 | displayable | `35b4e2600c8a6ea6` |
| `runa-okonkwo-f02` | runa-okonkwo | `current_work` | `github` | 0.88 | displayable | `9011fe302dab10ba` |
| `runa-okonkwo-f03` | runa-okonkwo | `hook` | `podcast` | 0.91 | displayable | `4fa82f5ab51b8b02` |
| `runa-okonkwo-f04` | runa-okonkwo | `hook` | `hn` | 0.78 | displayable | `5d952930adb32fe7` |
| `runa-okonkwo-f05` | runa-okonkwo | `recent_activity` | `search` | 0.86 | displayable | `92b1d32390d8795f` |
| `runa-okonkwo-f06` | runa-okonkwo | `recent_activity` | `openalex` | 0.84 | displayable | `64285175617dde55` |
| `runa-okonkwo-f07` | runa-okonkwo | `recent_activity` | `hn` | 0.82 | displayable | `5d952930adb32fe7` |
| `runa-okonkwo-f08` | runa-okonkwo | `recent_activity` | `github` | 0.8 | displayable | `9011fe302dab10ba` |
| `runa-okonkwo-f09` | runa-okonkwo | `non_obvious` | `wayback` | 0.87 | displayable | `d9902fb9cd225788` |
| `runa-okonkwo-f10` | runa-okonkwo | `affiliation` | `self_page` | 0.9 | displayable | `35b4e2600c8a6ea6` |
| `runa-okonkwo-f11` | runa-okonkwo | `affiliation` | `search` | 0.85 | displayable | `92b1d32390d8795f` |
| `runa-okonkwo-f12` | runa-okonkwo | `interest` | `search` | 0.9 | **EXCLUDED family** | `de86db5a839147e2` |
| `runa-okonkwo-f13` | runa-okonkwo | `interest` | `search` | 0.88 | **EXCLUDED home_or_property** | `babaa3f0a06e9dfe` |
| `runa-okonkwo-f14` | runa-okonkwo | `interest` | `search` | 0.55 | **not displayable** | `cf0c86082dfcc081` |
| `runa-okonkwo-f15` | runa-okonkwo | `affiliation` | `fec` | 0.92 | **not displayable** | `31173fc736e73821` |
| `runa-okonkwo-f16` | runa-okonkwo | `interest` | `self_page` | 0.82 | displayable | `35b4e2600c8a6ea6` |
| `runa-okonkwo-f17` | runa-okonkwo | `interest` | `self_page` | 0.8 | displayable | `35b4e2600c8a6ea6` |
| `sil-vantorre-f01` | sil-vantorre | `current_work` | `self_page` | 0.92 | displayable | `8e95f057ad101a20` |
| `sil-vantorre-f02` | sil-vantorre | `affiliation` | `self_page` | 0.9 | displayable | `8e95f057ad101a20` |
| `sil-vantorre-f03` | sil-vantorre | `interest` | `self_page` | 0.84 | displayable | `8e95f057ad101a20` |
| `sil-vantorre-f04` | sil-vantorre | `recent_activity` | `search` | 0.86 | displayable | `d3541f5a1b10b96a` |
| `theo-baptiste-f01` | theo-baptiste | `current_work` | `self_page` | 0.9 | displayable | `bd95ab53aac6c458` |
| `theo-baptiste-f02` | theo-baptiste | `affiliation` | `self_page` | 0.85 | displayable | `bd95ab53aac6c458` |
| `theo-baptiste-f03` | theo-baptiste | `interest` | `self_page` | 0.8 | displayable | `bd95ab53aac6c458` |
| `theo-baptiste-f04` | theo-baptiste | `recent_activity` | `search` | 0.83 | displayable | `8aca032fc32f4221` |

The three non-displayable kept facts are the discriminating ones:

* `runa-okonkwo-f14` - `search` (a **whitelisted** kind) at confidence **0.55**. Only the
  0.7 display floor stops it. Proves `is_displayable` gates on confidence, not only on
  `excluded`.
* `runa-okonkwo-f15` - confidence **0.92** (well over the floor), `excluded: false`, but
  `source_kind: fec`. Only the whitelist stops it. Proves the source-kind gate bites
  independently of the taste categories. It is deliberately left un-excluded: the
  fixture models defence in depth, where the taste layer did *not* catch something and
  the whitelist is the backstop. **Do not "tidy" `excluded` to `true`** - see
  *Disagreements* section 1b for the frozen test this would silently defeat.
* `runa-okonkwo-f12` / `f13` - `search` at 0.90 / 0.88, i.e. a whitelisted kind well over
  the floor. **`excluded` is the only thing withholding them**, which is exactly what the
  T-8 grep is testing.

---

## Disagreements with the spec, and judgment calls

Nothing here was silently adjusted to make a number come out.

### 1. `topic:developer-tools-gtm` violated DESIGN's hub-id canonicalisation (FIXED)

**Status: corrected before the freeze.** The hub is now
`topic:developer-tools-go-to-market`. Check 7, added for this, asserts the invariant over
the whole corpus and fails loudly if it is ever broken again.

DESIGN's `Hub` contract says `hub_id` is `"{type}:{slug(label)}"` when not
Wikidata-resolved, and the frozen T-3 suite grades the extractor on exactly that
canonicalisation (`test_wikidata_sourced_hub_is_keyed_by_its_qid` asserts
`company:quarrystone-labs` from the label "Quarrystone Labs"). As first committed, this
corpus carried hub_id `topic:developer-tools-gtm` against label
`"Developer-tools go-to-market"` in `runa-okonkwo.json` and `jem-arrowood.json` - two of
the sixteen hub entries, and the only two that broke the invariant. Check 7 run against a
reconstruction of the pre-fix corpus, verbatim and unedited, as a negative control (exit 1):

```
A. HUB ID CANONICALISATION - DESIGN Hub.hub_id = '{type}:{slug(label)}'
  hub_id                               label                          {type}:{slug(label)}                
  city:austin                          'Austin'                       city:austin                          OK
  company:lantern-freight              'Lantern Freight'              company:lantern-freight              OK
  investor:foundry-seed-2019           'Foundry Seed 2019'            investor:foundry-seed-2019           OK
  school:bellhaven-polytechnic         'Bellhaven Polytechnic'        school:bellhaven-polytechnic         OK
  topic:developer-tools-gtm            'Developer-tools go-to-market' topic:developer-tools-go-to-market   MISMATCH
  topic:remote-work                    'Remote work'                  topic:remote-work                    OK
  every hub_id is canonical                                      FAIL   16 hub entries, 6 distinct hubs, 2 mismatches
```

That made the corpus something a correct pipeline could never produce: given that label,
`{type}:{slug(label)}` is `topic:developer-tools-go-to-market`, so every T-5 or T-7
criterion keyed on the old id was grading implementations against a hub the extractor
cannot emit - the mirror image of gameability, a correct implementation graded red.

**Fixed by changing the ID, not the LABEL** - the reverse of what an earlier revision of
this file recommended. That recommendation is withdrawn, for two reasons:

* **Its premise was false, and was measured to be false.** It said not to touch the id
  because "the T-5 grading text names it". Before the fix, `grep -rn
  'topic:developer-tools-gtm'` over the whole worktree (excluding `.git/` and `.venv/`)
  found the string in exactly five files: the two dossiers, `test_t7_digest.py` (one line,
  in `_four_matches`), this file, and `.swarm-loop/findings.jsonl` - an append-only audit
  log that no test reads. The first three are the only executable sites, all belong to one
  owner, and all were changed in one commit. The string appears **nowhere** in
  `test_t5_matching.py`, which pins only `RARE_HUB_ID = "investor:foundry-seed-2019"` and
  `RARE_HUB_LABEL = "Foundry Seed 2019"` and recomputes every other hub generically by
  whatever id the corpus carries; and **nowhere** in `test_t8_web.py`, which names only the
  investor hub's label and weight. (One `findings.jsonl` record goes further and cites a
  specific line of `test_t5_matching.py` as pinning the topic id. No line of that file
  contains the string at all - `grep -c 'developer-tools' test_t5_matching.py` is 0 - and
  that false citation is why the wrong fix was recommended.)

  What T-5 *does* require is that the id be **identical in both dossiers** - `_expected_raw`
  intersects the two hub-id sets, so renaming in only one file would silently drop
  `runa x jem` from 67 to 0. Both files were renamed in the same commit, and check 1
  re-derives the 67 from the corpus as committed.
* **The label is the evidence; the id is derived from it.** The phrase "developer-tools
  go-to-market" appears verbatim in both source RawDocs (`50957dd279c64c59`,
  `92b1d32390d8795f`) and in the evidence facts' `text` and `quote`. Abbreviating the label
  to "Developer-tools GTM" to match a hand-written id would have restored internal
  consistency by making the **label** the thing no extractor would emit from these
  documents. The contract derives the id from the label, so the derived value is the one
  to correct.

**No graded number moved, and that is measured, not asserted.** Scoring reaches `hub_id`
only through set intersection, and membership is unchanged at 2 carriers (runa, jem), so
the weight stays `max(0, ln(5 / (1 + 2))) = 0.510826`, `runa x jem` stays **67**, and the
weighted shortest path stays cost 1.323780 with the new id as its middle node. Check 1 was
re-run against the edited corpus: across its 75 lines of output the only differences are
the hub-id string on three lines.

### 1b. Load-bearing corpus values that must never be "tidied"

Two values in this corpus look like oversights and are not. Both are cited by frozen tests
that would keep passing - for the wrong reason - if someone "corrected" them.

* **`runa-okonkwo-f15.excluded` is `false`, and must stay `false`.** The fact is a political
  contribution recorded in an FEC filing, so it reads like something the taste filter (R11)
  should have marked excluded. It is deliberately left unexcluded because its
  `source_kind` is `fec`, which is off the R12 display whitelist: it is blocked by the
  **display gate**, not by the taste filter. `test_t4_taste.py` takes it as `bad_kind` and
  asserts `bad_kind.excluded is False` **while** `is_displayable(bad_kind) is False`. That
  pair is the only proof in the whole frozen suite that the source-kind gate is
  independent of the taste filter rather than the taste filter wearing a second name.
  Setting `excluded: true` would leave that test green while destroying what it measures.
  (`runa-okonkwo-f14` plays the same role for the confidence floor: kept, not excluded,
  blocked at 0.55 < 0.7. Check 6 asserts both, "for two DIFFERENT reasons".)
* **`runa-okonkwo-f05` and `runa-okonkwo-f11` share the date 2026-02-11, the tie cannot be
  broken, and it is NOT a grading ambiguity - which is worth stating because it looks like
  one.** Both facts are extracted from RawDoc `92b1d32390d8795f`, and
  `provenance.published_at` equals its RawDoc's `published_at` for all 35 facts in this
  corpus (check 7, section D), so re-dating either one would put the fixture in
  contradiction with its own source document. But with three Lately slots to fill, **f05
  survives every reading of the candidate set the binding documents support**:

  | reading | candidates | top 3 | f05 in? |
  |---|---|---|---|
  | narrowest: `recent_activity` only | f05, f06, f07, f08 | f05, f06, f07 | yes, as the newest |
  | TASKS T-7 acc. 1: every `is_displayable` fact, `published_at` desc | 13 | f05 and f11 (both 2026-02-11) plus one of the four dated 2026-01-05 | yes - the tie puts BOTH in the first two slots |
  | anything between (e.g. + `affiliation`) | 6 | f05, f11, f10 | yes, same reason |

  The one shape that could show f11 without f05 is a Lately deduplicated by `doc_id`, and
  nothing asks for that: R9/S6 dedupe the **source list** by `doc_id`, never the bullets.
  The frozen suite also pins f05 in a second place - `test_t8_web.py` `DISPLAYED[1]` is
  f05's `text` verbatim and is asserted to appear in the rendered digest, and Lately is the
  only section f05 can reach (`who_line` comes from `current_work`, `non_obvious` is f09,
  `say_out_loud` from a `hook`). So `test_t7_digest.py` pins f05 too, and the two modules
  grade the same digest the same way. **An earlier revision of this file relaxed T-7 to
  "either tied fact will do"; that was withdrawn** because it made T-7 and T-8 contradict
  each other and, as a side effect, allowed a Lately containing no `recent_activity` fact at
  all (`[f11, f16, f17]` would have scored). What survives from that attempt is the corpus
  self-check in `test_lately_is_capped_at_three_and_ordered_most_recent_first`, which pins
  the shape the f05 assertion depends on and fails loudly if the corpus stops matching it.

  Note also that check 6's line "lately ordering is unambiguous" is scoped to the four
  `recent_activity` facts; it is not a claim about the wider candidate set that TASKS T-7
  acceptance 1 permits.

### 2. "confidence" in section 1b means `provenance.confidence`

Section 1b writes things like "2 `current_work` facts, confidence >= 0.8", but the
DESIGN `Fact` model has **no** `confidence` field - confidence lives on `Provenance`.
Read literally, those requirements are unsatisfiable. They are implemented as
`fact.provenance.confidence`, which is also what R12 and `is_displayable` read.

### 3. A latent collision with the spec's own section-3 example - measured, and clear

Frozen-spec section 3 prints an example taste case reading
`"Lives at 1442 Quarrystone Lane in the Bouldin Creek neighbourhood."`. Had the
section-3 author written that case verbatim, `1442 Quarrystone Lane` would appear a
fourth time under `fixtures/` and a whole-tree `grep -c` would read 4, not 3.

**Measured against the tree as it now stands** (`taste_cases_frozen.yaml` and
`resolve_cases/` have both landed): all five distinctive strings appear **zero** times
in the sibling corpora. The taste corpus does reuse the invented company name
`Quarrystone Labs` in two cases, which is harmless and in fact desirable - one cast,
one universe. So each string's count across the whole fixture tree is exactly 3.

The one remaining caveat is **this file**: `CORPUS-PROOF.md` lives under `fixtures/` and
quotes all five strings as evidence. Check 4's scan therefore excludes it by name, and a
frozen test that greps the tree must do the same. The T-8 test greps *rendered HTML*,
which contains neither this file nor the taste YAML, so nothing downstream is affected.

### 4. "Foundry Seed" shares a token with a real firm on the roster

`data/roster.yaml` lists Brad Feld as co-founder of **Foundry Group**. The frozen spec
mandates the invented investor **Foundry Seed 2019**. They are different names and no
roster person, roster company or roster domain appears anywhere in this corpus (check 5,
40 exact-match probes - 10 full names, 10 surnames, 20 roster companies/domains - all
absent). Flagged so a human reviewer is not surprised by the shared word.

### 5. Deliberate authoring choices, recorded so nobody has to guess later

* **Every hub carries `recency: 1.0`.** Section 1a *requires* 1.0 on both edges of the
  two scoring hubs. Varying recency on the two clamped hubs would change nothing (their
  contribution is `0 * recency * boost = 0` either way), so variation would buy no test
  discrimination while creating a chance for a sibling test to assume the wrong value.
* **`mira` and `theo` share only the two clamped hubs**, so their score is 0 with every
  contribution 0 - and each also carries one hub nobody else has, so a broken
  implementation cannot get 0 by accident.
* **Provenance `source_kind` always equals its RawDoc's `source_kind`**, and
  `provenance.published_at` always equals its RawDoc's `published_at`. The contract does
  not require either, but a fixture that disagreed with itself would be a trap.
* **`resolution.accepted_doc_ids` is exactly the set of doc_ids that person's facts
  cite**, in first-citation order. Asserted in check 3.
* **`runa`'s excluded facts use category `interest`, not a taste category** - `Fact` has
  no taste category field; the taste verdict lives in `excluded` + `exclusion_reason`.
* **The four `recent_activity` dates are strictly ordered** and the `<=3` cap drops
  exactly one, `2025-05-27`, so an off-by-one in `lately` is visible.
* **The highest-confidence hook is unambiguous** (0.91 vs 0.78), so the `say_out_loud`
  template fallback has one and only one correct answer: `runa-okonkwo-f03`.

---

## Check 1 - Hub arithmetic, recomputed from scratch (spec section 1a)

### Script (`check1_hub_arithmetic.py`)

```python
#!/usr/bin/env python3
"""CHECK 1 - hub arithmetic, recomputed from scratch against the committed JSON.

Independent of any product code. Implements DESIGN Decision 3 and the frozen spec
section 1a directly, plus its own Dijkstra (heapq, stdlib) for the "why" path.
Nothing here is taken on trust from the spec: the assertions at the bottom are the
hypothesis, the computation above them is the measurement.
"""

import heapq
import json
import math
from pathlib import Path

DOSSIERS = Path(
    "/Users/hankholcomb/Documents/code_parent_folders/gauntlet_repos/arrivalengine"
    "/.swarm-loop/acceptance/fixtures/dossiers"
)

TYPE_BOOST = {
    "investor": 1.5, "board": 1.5, "company": 1.5,
    "event": 1.3, "cause": 1.3, "person": 1.3,
    "technology": 1.0, "topic": 1.0,
    "school": 0.8,
    "city": 0.5,
}

# ---------------------------------------------------------------- load
people = {}
for p in sorted(DOSSIERS.glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    people[d["person"]["person_id"]] = d

N = len(people)
print("N (person nodes in the graph) = %d" % N)
print("people = %s" % sorted(people))
print()

# ---------------------------------------------------------------- carriers
carriers = {}          # hub_id -> set(person_id)
hub_meta = {}          # hub_id -> (label, type)
recency = {}           # (person_id, hub_id) -> recency
for pid, d in people.items():
    for h in d["hubs"]:
        carriers.setdefault(h["hub_id"], set()).add(pid)
        hub_meta[h["hub_id"]] = (h["label"], h["type"])
        recency[(pid, h["hub_id"])] = h["recency"]

# ---------------------------------------------------------------- idf
idf = {}
print("%-32s %-26s %-9s %-3s %-10s %-10s %s" %
      ("hub_id", "label", "type", "n", "idf", "boost", "carriers"))
for hid in sorted(carriers):
    n = len(carriers[hid])
    raw_idf = math.log(N / (1 + n))
    idf[hid] = max(0.0, raw_idf)
    label, htype = hub_meta[hid]
    print("%-32s %-26s %-9s %-3d %-10.6f %-10s %s" %
          (hid, label, htype, n, idf[hid], TYPE_BOOST[htype],
           ",".join(sorted(carriers[hid]))))
    print("%-32s   ln(%d/(1+%d)) = %.6f -> clamp max(0, .) = %.6f" %
          ("", N, n, raw_idf, idf[hid]))
print()

REF = math.log(N / 3) * 1.5
print("REF = ln(N/3) * 1.5 = ln(%d/3) * 1.5 = %.6f" % (N, REF))
print()

# ---------------------------------------------------------------- pairwise
def contributions(a, b):
    out = []
    shared = sorted(set(h["hub_id"] for h in people[a]["hubs"])
                    & set(h["hub_id"] for h in people[b]["hubs"]))
    for hid in shared:
        r = min(recency[(a, hid)], recency[(b, hid)])
        boost = TYPE_BOOST[hub_meta[hid][1]]
        out.append((hid, idf[hid], r, boost, idf[hid] * r * boost))
    out.sort(key=lambda t: -t[4])
    return out


def score(a, b):
    raw = sum(c[4] for c in contributions(a, b))
    return raw, min(100, round(100 * raw / REF))


order = sorted(people)
print("all pairwise scores")
for i, a in enumerate(order):
    for b in order[i + 1:]:
        raw, sc = score(a, b)
        cs = contributions(a, b)
        print("  %-16s x %-16s raw=%.6f  score=%s" % (a, b, raw, sc))
        for hid, w, r, boost, contrib in cs:
            print("      %-32s idf=%.6f recency=%.2f boost=%.1f contribution=%.6f"
                  % (hid, w, r, boost, contrib))
print()

# ---------------------------------------------------------------- shortest path
def dijkstra(src, dst):
    """cost = 1/(1+idf) on every person-hub edge."""
    adj = {}
    for pid, d in people.items():
        for h in d["hubs"]:
            c = 1.0 / (1.0 + idf[h["hub_id"]])
            adj.setdefault("person:" + pid, []).append(("hub:" + h["hub_id"], c))
            adj.setdefault("hub:" + h["hub_id"], []).append(("person:" + pid, c))
    dist = {src: 0.0}
    prev = {}
    pq = [(0.0, src)]
    seen = set()
    while pq:
        d0, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == dst:
            break
        for v, c in adj.get(u, []):
            nd = d0 + c
            if nd < dist.get(v, float("inf")) - 1e-12:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if dst not in dist:
        return None, None
    path, cur = [dst], dst
    while cur != src:
        cur = prev[cur]
        path.append(cur)
    return list(reversed(path)), dist[dst]


for other in ["sil-vantorre", "jem-arrowood", "mira-hollowell", "theo-baptiste"]:
    path, cost = dijkstra("person:runa-okonkwo", "person:" + other)
    print("shortest path runa-okonkwo -> %-16s cost=%.6f  %s" % (other, cost, path))
print()

# cross-check the path with networkx if it happens to be installed
try:
    import networkx as nx
    g = nx.Graph()
    for pid, d in people.items():
        for h in d["hubs"]:
            g.add_edge("person:" + pid, "hub:" + h["hub_id"],
                       cost=1.0 / (1.0 + idf[h["hub_id"]]))
    print("networkx %s cross-check: %s" % (
        nx.__version__,
        nx.shortest_path(g, "person:runa-okonkwo", "person:sil-vantorre", weight="cost")))
except ImportError:
    print("networkx not installed; own-Dijkstra result stands alone")
print()

# ---------------------------------------------------------------- assertions
print("ASSERTIONS")
ranked = sorted(
    ["mira-hollowell", "theo-baptiste", "sil-vantorre", "jem-arrowood"],
    key=lambda o: -score("runa-okonkwo", o)[1],
)
checks = []


def check(name, cond, detail):
    checks.append((name, cond, detail))
    print("  %-42s %s   %s" % (name, "PASS" if cond else "FAIL", detail))


s_sil = score("runa-okonkwo", "sil-vantorre")[1]
s_jem = score("runa-okonkwo", "jem-arrowood")[1]
s_mira = score("runa-okonkwo", "mira-hollowell")[1]
s_theo = score("runa-okonkwo", "theo-baptiste")[1]
s_mt = score("mira-hollowell", "theo-baptiste")[1]

check("runa x sil == 100", s_sil == 100, "got %s" % s_sil)
check("runa x jem == 67", s_jem == 67, "got %s" % s_jem)
check("mira x theo == 0", s_mt == 0, "got %s" % s_mt)
check("mira x theo all contributions zero",
      all(c[4] == 0 for c in contributions("mira-hollowell", "theo-baptiste")),
      str([c[4] for c in contributions("mira-hollowell", "theo-baptiste")]))
check("ranking sil > jem > {mira,theo}",
      s_sil > s_jem > s_mira and s_mira == s_theo == 0,
      "sil=%s jem=%s mira=%s theo=%s" % (s_sil, s_jem, s_mira, s_theo))
check("ranked order", ranked[0] == "sil-vantorre" and ranked[1] == "jem-arrowood",
      str(ranked))
top = contributions("runa-okonkwo", "sil-vantorre")[0][0]
check("sil top contribution hub is the investor hub",
      top == "investor:foundry-seed-2019", top)
p, _ = dijkstra("person:runa-okonkwo", "person:sil-vantorre")
check("path runa->sil via investor hub",
      p == ["person:runa-okonkwo", "hub:investor:foundry-seed-2019", "person:sil-vantorre"],
      str(p))
check("city:austin idf clamped to 0", idf["city:austin"] == 0.0, str(idf["city:austin"]))
check("topic:remote-work idf clamped to 0", idf["topic:remote-work"] == 0.0,
      str(idf["topic:remote-work"]))
check("unique hubs carried by exactly 1 person",
      all(len(carriers[h]) == 1 for h in
          ["company:lantern-freight", "school:bellhaven-polytechnic"]),
      "lantern=%d bellhaven=%d" % (len(carriers["company:lantern-freight"]),
                                   len(carriers["school:bellhaven-polytechnic"])))
check("every hub evidence_fact_id exists in its own dossier",
      all(fid in {f["fact_id"] for f in people[pid]["facts"]}
          for pid, d in people.items() for h in d["hubs"] for fid in h["evidence_fact_ids"]),
      "checked %d references" % sum(len(h["evidence_fact_ids"])
                                    for d in people.values() for h in d["hubs"]))

bad = [c for c in checks if not c[1]]
print()
print("CHECK 1 RESULT: %d/%d assertions passed" % (len(checks) - len(bad), len(checks)))
raise SystemExit(1 if bad else 0)
```

### Verbatim output (exit code 0)

```text
N (person nodes in the graph) = 5
people = ['jem-arrowood', 'mira-hollowell', 'runa-okonkwo', 'sil-vantorre', 'theo-baptiste']

hub_id                           label                      type      n   idf        boost      carriers
city:austin                      Austin                     city      5   0.000000   0.5        jem-arrowood,mira-hollowell,runa-okonkwo,sil-vantorre,theo-baptiste
                                   ln(5/(1+5)) = -0.182322 -> clamp max(0, .) = 0.000000
company:lantern-freight          Lantern Freight            company   1   0.916291   1.5        mira-hollowell
                                   ln(5/(1+1)) = 0.916291 -> clamp max(0, .) = 0.916291
investor:foundry-seed-2019       Foundry Seed 2019          investor  2   0.510826   1.5        runa-okonkwo,sil-vantorre
                                   ln(5/(1+2)) = 0.510826 -> clamp max(0, .) = 0.510826
school:bellhaven-polytechnic     Bellhaven Polytechnic      school    1   0.916291   0.8        theo-baptiste
                                   ln(5/(1+1)) = 0.916291 -> clamp max(0, .) = 0.916291
topic:developer-tools-go-to-market Developer-tools go-to-market topic     2   0.510826   1.0        jem-arrowood,runa-okonkwo
                                   ln(5/(1+2)) = 0.510826 -> clamp max(0, .) = 0.510826
topic:remote-work                Remote work                topic     5   0.000000   1.0        jem-arrowood,mira-hollowell,runa-okonkwo,sil-vantorre,theo-baptiste
                                   ln(5/(1+5)) = -0.182322 -> clamp max(0, .) = 0.000000

REF = ln(N/3) * 1.5 = ln(5/3) * 1.5 = 0.766238

all pairwise scores
  jem-arrowood     x mira-hollowell   raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  jem-arrowood     x runa-okonkwo     raw=0.510826  score=67
      topic:developer-tools-go-to-market idf=0.510826 recency=1.00 boost=1.0 contribution=0.510826
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  jem-arrowood     x sil-vantorre     raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  jem-arrowood     x theo-baptiste    raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  mira-hollowell   x runa-okonkwo     raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  mira-hollowell   x sil-vantorre     raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  mira-hollowell   x theo-baptiste    raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  runa-okonkwo     x sil-vantorre     raw=0.766238  score=100
      investor:foundry-seed-2019       idf=0.510826 recency=1.00 boost=1.5 contribution=0.766238
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  runa-okonkwo     x theo-baptiste    raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000
  sil-vantorre     x theo-baptiste    raw=0.000000  score=0
      city:austin                      idf=0.000000 recency=1.00 boost=0.5 contribution=0.000000
      topic:remote-work                idf=0.000000 recency=1.00 boost=1.0 contribution=0.000000

shortest path runa-okonkwo -> sil-vantorre     cost=1.323780  ['person:runa-okonkwo', 'hub:investor:foundry-seed-2019', 'person:sil-vantorre']
shortest path runa-okonkwo -> jem-arrowood     cost=1.323780  ['person:runa-okonkwo', 'hub:topic:developer-tools-go-to-market', 'person:jem-arrowood']
shortest path runa-okonkwo -> mira-hollowell   cost=2.000000  ['person:runa-okonkwo', 'hub:city:austin', 'person:mira-hollowell']
shortest path runa-okonkwo -> theo-baptiste    cost=2.000000  ['person:runa-okonkwo', 'hub:city:austin', 'person:theo-baptiste']

networkx 2.6.3 cross-check: ['person:runa-okonkwo', 'hub:investor:foundry-seed-2019', 'person:sil-vantorre']

ASSERTIONS
  runa x sil == 100                          PASS   got 100
  runa x jem == 67                           PASS   got 67
  mira x theo == 0                           PASS   got 0
  mira x theo all contributions zero         PASS   [0.0, 0.0]
  ranking sil > jem > {mira,theo}            PASS   sil=100 jem=67 mira=0 theo=0
  ranked order                               PASS   ['sil-vantorre', 'jem-arrowood', 'mira-hollowell', 'theo-baptiste']
  sil top contribution hub is the investor hub PASS   investor:foundry-seed-2019
  path runa->sil via investor hub            PASS   ['person:runa-okonkwo', 'hub:investor:foundry-seed-2019', 'person:sil-vantorre']
  city:austin idf clamped to 0               PASS   0.0
  topic:remote-work idf clamped to 0         PASS   0.0
  unique hubs carried by exactly 1 person    PASS   lantern=1 bellhaven=1
  every hub evidence_fact_id exists in its own dossier PASS   checked 16 references

CHECK 1 RESULT: 12/12 assertions passed
```

---

## Check 2 - Every quote is a normalize_ws substring of its RawDoc (spec section 1b, DESIGN Decision 5)

### Script (`check2_citations.py`)

```python
#!/usr/bin/env python3
"""CHECK 2 - every Fact's provenance.quote is a normalize_ws substring of its RawDoc text.

normalize_ws is reimplemented here from its DESIGN Decision 5 definition (collapse all
whitespace runs to a single space, strip, casefold) rather than imported, because at
freeze time arrival.util does not exist yet and because the grading fixture must not
depend on the gradee's implementation of the very function it is graded on.
"""

import json
import re
from pathlib import Path

FIX = Path(
    "/Users/hankholcomb/Documents/code_parent_folders/gauntlet_repos/arrivalengine"
    "/.swarm-loop/acceptance/fixtures"
)


def normalize_ws(s):
    return re.sub(r"\s+", " ", s).strip().casefold()


docs = {}
for p in sorted((FIX / "docs").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    docs[d["doc_id"]] = d

dossier_files = sorted((FIX / "dossiers").glob("*.json")) + \
                sorted((FIX / "dossiers_unresolved").glob("*.json"))

n_facts = 0
failures = []
print("%-26s %-18s %-10s %-5s %s" % ("fact_id", "doc_id", "kind", "conf", "quote ok"))
for p in dossier_files:
    d = json.loads(p.read_text(encoding="utf-8"))
    for f in d["facts"]:
        n_facts += 1
        pr = f["provenance"]
        doc = docs.get(pr["doc_id"])
        if doc is None:
            failures.append((f["fact_id"], "doc_id not in fixtures/docs/"))
            print("%-26s %-18s %-10s %-5s MISSING DOC" %
                  (f["fact_id"], pr["doc_id"], pr["source_kind"], pr["confidence"]))
            continue
        ok = normalize_ws(pr["quote"]) in normalize_ws(doc["text"])
        if not ok:
            failures.append((f["fact_id"], "quote not a normalize_ws substring"))
        print("%-26s %-18s %-10s %-5s %s" %
              (f["fact_id"], pr["doc_id"], pr["source_kind"], pr["confidence"],
               "yes" if ok else "NO  <-- FAILURE"))

print()
print("also checking the source_kind on each Provenance equals the RawDoc's own kind")
kind_fail = []
for p in dossier_files:
    d = json.loads(p.read_text(encoding="utf-8"))
    for f in d["facts"]:
        pr = f["provenance"]
        doc = docs.get(pr["doc_id"])
        if doc and doc["source_kind"] != pr["source_kind"]:
            kind_fail.append((f["fact_id"], pr["source_kind"], doc["source_kind"]))
print("  provenance/doc source_kind mismatches: %d %s" % (len(kind_fail), kind_fail or ""))

print()
print("also checking resolution.rejected verdict evidence spans (not required by the")
print("spec's four checks, but a Verdict.evidence is contractually a verbatim span):")
verdict_fail = []
n_verdicts = 0
for p in dossier_files:
    d = json.loads(p.read_text(encoding="utf-8"))
    for v in d["resolution"]["rejected"]:
        n_verdicts += 1
        doc = docs.get(v["doc_id"])
        ok = doc is not None and normalize_ws(v["evidence"]) in normalize_ws(doc["text"])
        print("  %-18s %-7s %s" % (v["doc_id"], v["match"], "yes" if ok else "NO"))
        if not ok:
            verdict_fail.append(v["doc_id"])

print()
print("facts checked: %d over %d dossiers; docs available: %d" %
      (n_facts, len(dossier_files), len(docs)))
print("verdicts checked: %d" % n_verdicts)
print("CHECK 2 RESULT: %d quote failures, %d source_kind mismatches, %d verdict failures"
      % (len(failures), len(kind_fail), len(verdict_fail)))
raise SystemExit(1 if (failures or kind_fail or verdict_fail) else 0)
```

### Verbatim output (exit code 0)

```text
fact_id                    doc_id             kind       conf  quote ok
jem-arrowood-f01           50957dd279c64c59   self_page  0.91  yes
jem-arrowood-f02           50957dd279c64c59   self_page  0.86  yes
jem-arrowood-f03           50957dd279c64c59   self_page  0.83  yes
jem-arrowood-f04           137675365d8ea470   search     0.87  yes
mira-hollowell-f01         d07ba24408f2aa13   self_page  0.9   yes
mira-hollowell-f02         b46212cb0a5a8c0c   search     0.85  yes
mira-hollowell-f03         d07ba24408f2aa13   self_page  0.82  yes
mira-hollowell-f04         b46212cb0a5a8c0c   search     0.84  yes
mira-hollowell-f05         ca6310f83f9a9387   search     0.86  yes
mira-hollowell-f06         ca6310f83f9a9387   search     0.84  yes
runa-okonkwo-f01           35b4e2600c8a6ea6   self_page  0.93  yes
runa-okonkwo-f02           9011fe302dab10ba   github     0.88  yes
runa-okonkwo-f03           4fa82f5ab51b8b02   podcast    0.91  yes
runa-okonkwo-f04           5d952930adb32fe7   hn         0.78  yes
runa-okonkwo-f05           92b1d32390d8795f   search     0.86  yes
runa-okonkwo-f06           64285175617dde55   openalex   0.84  yes
runa-okonkwo-f07           5d952930adb32fe7   hn         0.82  yes
runa-okonkwo-f08           9011fe302dab10ba   github     0.8   yes
runa-okonkwo-f09           d9902fb9cd225788   wayback    0.87  yes
runa-okonkwo-f10           35b4e2600c8a6ea6   self_page  0.9   yes
runa-okonkwo-f11           92b1d32390d8795f   search     0.85  yes
runa-okonkwo-f12           de86db5a839147e2   search     0.9   yes
runa-okonkwo-f13           babaa3f0a06e9dfe   search     0.88  yes
runa-okonkwo-f14           cf0c86082dfcc081   search     0.55  yes
runa-okonkwo-f15           31173fc736e73821   fec        0.92  yes
runa-okonkwo-f16           35b4e2600c8a6ea6   self_page  0.82  yes
runa-okonkwo-f17           35b4e2600c8a6ea6   self_page  0.8   yes
sil-vantorre-f01           8e95f057ad101a20   self_page  0.92  yes
sil-vantorre-f02           8e95f057ad101a20   self_page  0.9   yes
sil-vantorre-f03           8e95f057ad101a20   self_page  0.84  yes
sil-vantorre-f04           d3541f5a1b10b96a   search     0.86  yes
theo-baptiste-f01          bd95ab53aac6c458   self_page  0.9   yes
theo-baptiste-f02          bd95ab53aac6c458   self_page  0.85  yes
theo-baptiste-f03          bd95ab53aac6c458   self_page  0.8   yes
theo-baptiste-f04          8aca032fc32f4221   search     0.83  yes

also checking the source_kind on each Provenance equals the RawDoc's own kind
  provenance/doc source_kind mismatches: 0 

also checking resolution.rejected verdict evidence spans (not required by the
spec's four checks, but a Verdict.evidence is contractually a verbatim span):
  e4ba96415536ce5f   no      yes
  4583e496f241803c   unsure  yes
  22b557df95a72095   unsure  yes

facts checked: 35 over 6 dossiers; docs available: 23
verdicts checked: 3
CHECK 2 RESULT: 0 quote failures, 0 source_kind mismatches, 0 verdict failures
```

---

## Check 3 - doc_id / url / sha1 integrity (spec section 2)

### Script (`check3_doc_ids.py`)

```python
#!/usr/bin/env python3
"""CHECK 3 - doc_id integrity.

  a. every Provenance.doc_id names a file that exists in fixtures/docs/
  b. every Provenance.url equals that file's url field
  c. every doc_id equals sha1(url.encode()).hexdigest()[:16]
  d. every docs/<name>.json has name == its own doc_id field
  e. RawDoc invariants from the contract: text never empty, <= 20k chars
"""

import hashlib
import json
from pathlib import Path

FIX = Path(
    "/Users/hankholcomb/Documents/code_parent_folders/gauntlet_repos/arrivalengine"
    "/.swarm-loop/acceptance/fixtures"
)

fail = []
docs = {}
print("%-18s %-6s %-6s %-6s %s" % ("doc_id", "sha1?", "fname?", "chars", "url"))
for p in sorted((FIX / "docs").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    docs[d["doc_id"]] = d
    want = hashlib.sha1(d["url"].encode()).hexdigest()[:16]
    sha_ok = want == d["doc_id"]
    name_ok = p.stem == d["doc_id"]
    text_ok = 0 < len(d["text"]) <= 20000
    if not sha_ok:
        fail.append(("sha1", p.name, "expected %s" % want))
    if not name_ok:
        fail.append(("filename", p.name, d["doc_id"]))
    if not text_ok:
        fail.append(("text length", p.name, len(d["text"])))
    print("%-18s %-6s %-6s %-6d %s" %
          (d["doc_id"], "ok" if sha_ok else "BAD", "ok" if name_ok else "BAD",
           len(d["text"]), d["url"]))

print()
dossier_files = sorted((FIX / "dossiers").glob("*.json")) + \
                sorted((FIX / "dossiers_unresolved").glob("*.json"))
n = 0
for p in dossier_files:
    dd = json.loads(p.read_text(encoding="utf-8"))
    for f in dd["facts"]:
        n += 1
        pr = f["provenance"]
        if pr["doc_id"] not in docs:
            fail.append(("missing doc", f["fact_id"], pr["doc_id"]))
            continue
        if docs[pr["doc_id"]]["url"] != pr["url"]:
            fail.append(("url mismatch", f["fact_id"],
                         "%s != %s" % (pr["url"], docs[pr["doc_id"]]["url"])))
    for v in dd["resolution"]["rejected"]:
        if v["doc_id"] not in docs:
            fail.append(("missing doc (verdict)", p.stem, v["doc_id"]))
    for did in dd["resolution"]["accepted_doc_ids"]:
        if did not in docs:
            fail.append(("missing doc (accepted)", p.stem, did))
    cited = sorted({f["provenance"]["doc_id"] for f in dd["facts"]})
    if sorted(dd["resolution"]["accepted_doc_ids"]) != cited:
        fail.append(("accepted_doc_ids != cited docs", p.stem,
                     "%s vs %s" % (sorted(dd["resolution"]["accepted_doc_ids"]), cited)))

print("provenance entries checked: %d; docs on disk: %d" % (n, len(docs)))
orphans = set(docs) - {f["provenance"]["doc_id"]
                       for p in dossier_files
                       for f in json.loads(p.read_text(encoding="utf-8"))["facts"]}
print("docs not cited by any Fact (rejected/unresolved evidence, expected): %d" % len(orphans))
for o in sorted(orphans):
    print("    %s  %s" % (o, docs[o]["url"]))
print()
print("CHECK 3 RESULT: %d failures %s" % (len(fail), fail or ""))
raise SystemExit(1 if fail else 0)
```

### Verbatim output (exit code 0)

```text
doc_id             sha1?  fname? chars  url
137675365d8ea470   ok     ok     605    https://example.org/tradepress/2026/tallow-harbor-trial-rewrite
22b557df95a72095   ok     ok     269    https://example.com/github/vextarrow
31173fc736e73821   ok     ok     589    https://example.org/fec/filings/C00-4471902
35b4e2600c8a6ea6   ok     ok     930    https://example.com/runa-okonkwo/about
4583e496f241803c   ok     ok     370    https://example.org/newswire/2023/tarrow-one-room-gallery
4fa82f5ab51b8b02   ok     ok     798    https://example.org/podcasts/harbor-lines/episode-88
50957dd279c64c59   ok     ok     697    https://example.com/jem-arrowood/now
5d952930adb32fe7   ok     ok     645    https://example.org/hn/item?id=41220885
64285175617dde55   ok     ok     610    https://example.org/openalex/works/W2201194
8aca032fc32f4221   ok     ok     616    https://example.org/tradepress/2025/bellhaven-sensor-audit
8e95f057ad101a20   ok     ok     735    https://example.com/sil-vantorre
9011fe302dab10ba   ok     ok     678    https://example.com/github/quarrystone/cli
92b1d32390d8795f   ok     ok     759    https://example.org/tradepress/2026/quarrystone-platform-roadmap
b46212cb0a5a8c0c   ok     ok     598    https://example.org/tradepress/2025/lantern-freight-dispatch-rules
babaa3f0a06e9dfe   ok     ok     668    https://example.org/bouldin-ledger/2024/quarterly-renovation-permits
bd95ab53aac6c458   ok     ok     634    https://example.com/bellhaven-polytechnic/people/theo-baptiste
ca6310f83f9a9387   ok     ok     743    https://example.org/city-monthly/2024/the-dispatcher
cf0c86082dfcc081   ok     ok     652    https://example.org/harbor-notes/2025/ferry-timetable-thread
d07ba24408f2aa13   ok     ok     677    https://example.com/lantern-freight/team/mira-hollowell
d3541f5a1b10b96a   ok     ok     660    https://example.org/tradepress/2025/foundry-seed-vintage-review
d9902fb9cd225788   ok     ok     649    https://web.example.org/web/20170614/quarrystonelabs.example.com/status
de86db5a839147e2   ok     ok     688    https://example.org/city-monthly/2025/the-platform-builder
e4ba96415536ce5f   ok     ok     483    https://example.org/newswire/2019/okonkwo-named-deputy-harbourmaster

provenance entries checked: 35; docs on disk: 23
docs not cited by any Fact (rejected/unresolved evidence, expected): 3
    22b557df95a72095  https://example.com/github/vextarrow
    4583e496f241803c  https://example.org/newswire/2023/tarrow-one-room-gallery
    e4ba96415536ce5f  https://example.org/newswire/2019/okonkwo-named-deputy-harbourmaster

CHECK 3 RESULT: 0 failures 
```

---

## Check 4 - The five distinctive strings appear exactly where the spec puts them

### Script (`check4_distinctive_strings.py`)

```python
#!/usr/bin/env python3
"""CHECK 4 - the five distinctive strings the downstream tests grep for.

Each must appear EXACTLY where the frozen spec puts it and nowhere else. "Where the
spec says" resolves to three places per string, because sections 1b and 2 together
force all three:

    1. the Fact's `text`            (section 1b: "text must contain ...")
    2. the Fact's `provenance.quote`
    3. the paired RawDoc's `text`   (section 2: quote must be a substring of doc text)

The scan walks EVERY file under fixtures/ (any extension), counts raw occurrences per
file, and then asserts the exact per-field placement inside the corpus this agent owns
(dossiers/, dossiers_unresolved/, docs/). Files owned by sibling authors are reported
but not asserted on - see the note printed at the end.
"""

import json
from pathlib import Path

FIX = Path(
    "/Users/hankholcomb/Documents/code_parent_folders/gauntlet_repos/arrivalengine"
    "/.swarm-loop/acceptance/fixtures"
)
MINE = ("dossiers", "dossiers_unresolved", "docs")

STRINGS = {
    "their spouse Delia Moreno-Vance":
        ("runa-okonkwo-f12", "excluded family fact", "de86db5a839147e2"),
    "1442 Quarrystone Lane":
        ("runa-okonkwo-f13", "excluded home_or_property fact", "babaa3f0a06e9dfe"),
    "a low-confidence claim about ferry schedules":
        ("runa-okonkwo-f14", "not-excluded fact, provenance.confidence 0.55", "cf0c86082dfcc081"),
    "a contribution recorded in a filing":
        ("runa-okonkwo-f15", "not-excluded fact, source_kind fec", "31173fc736e73821"),
    "Quarrystone Labs shipped a public status page in 2017":
        ("runa-okonkwo-f09", "non_obvious fact, source_kind wayback", "d9902fb9cd225788"),
}

fail = []

# ------------------------------------------------ raw whole-tree occurrence scan
print("RAW OCCURRENCE SCAN over every file under fixtures/")
print("CORPUS-PROOF.md is excluded: it is this audit trail, not corpus, and it quotes")
print("all five strings as evidence - including it would make the scan self-referential")
print("(the count would depend on the file the count is written into).")
all_files = sorted(p for p in FIX.rglob("*")
                   if p.is_file() and p.name != "CORPUS-PROOF.md")
print("files scanned: %d" % len(all_files))
for s in STRINGS:
    print()
    print('  "%s"' % s)
    total = 0
    for p in all_files:
        try:
            body = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        c = body.count(s)
        if c:
            rel = p.relative_to(FIX)
            owner = "MINE " if rel.parts[0] in MINE else "OTHER"
            print("      %s %-3d %s" % (owner, c, rel))
            total += c
    print("      total raw occurrences across fixtures/: %d" % total)

# ------------------------------------------------ exact placement inside my corpus
print()
print("EXACT PLACEMENT inside the corpus this agent owns")
dossier_files = sorted((FIX / "dossiers").glob("*.json")) + \
                sorted((FIX / "dossiers_unresolved").glob("*.json"))
doc_files = sorted((FIX / "docs").glob("*.json"))

for s, (want_fact, why, want_doc) in STRINGS.items():
    hits = []          # (location, identifier)
    for p in dossier_files:
        d = json.loads(p.read_text(encoding="utf-8"))
        for f in d["facts"]:
            if s in f["text"]:
                hits.append(("fact.text", f["fact_id"]))
            if s in f["provenance"]["quote"]:
                hits.append(("fact.provenance.quote", f["fact_id"]))
            for k in ("fact_id", "category", "exclusion_reason"):
                if f.get(k) and s in str(f[k]):
                    hits.append(("fact.%s" % k, f["fact_id"]))
        for v in d["resolution"]["rejected"]:
            if s in v["evidence"]:
                hits.append(("verdict.evidence", v["doc_id"]))
    for p in doc_files:
        d = json.loads(p.read_text(encoding="utf-8"))
        if s in d["text"]:
            hits.append(("doc.text", d["doc_id"]))
        if s in d["title"]:
            hits.append(("doc.title", d["doc_id"]))

    expected = [("fact.text", want_fact),
                ("fact.provenance.quote", want_fact),
                ("doc.text", want_doc)]
    ok = sorted(hits) == sorted(expected)
    if not ok:
        fail.append((s, hits))
    print()
    print('  "%s"   (%s)' % (s, why))
    print("      expected: %s" % expected)
    print("      found   : %s" % sorted(hits))
    print("      %s" % ("PASS" if ok else "FAIL <-- placement differs"))

# ------------------------------------------------ the facts themselves
print()
print("FACT-LEVEL PROPERTIES the strings are supposed to demonstrate")
runa = json.loads((FIX / "dossiers" / "runa-okonkwo.json").read_text(encoding="utf-8"))
by_id = {f["fact_id"]: f for f in runa["facts"]}


def prop(name, cond, detail):
    if not cond:
        fail.append((name, detail))
    print("  %-56s %s   %s" % (name, "PASS" if cond else "FAIL", detail))


f12, f13, f14, f15, f09 = (by_id["runa-okonkwo-f12"], by_id["runa-okonkwo-f13"],
                           by_id["runa-okonkwo-f14"], by_id["runa-okonkwo-f15"],
                           by_id["runa-okonkwo-f09"])
prop("f12 excluded=True, reason 'family'",
     f12["excluded"] is True and f12["exclusion_reason"] == "family",
     "%s / %s" % (f12["excluded"], f12["exclusion_reason"]))
prop("f13 excluded=True, reason 'home_or_property'",
     f13["excluded"] is True and f13["exclusion_reason"] == "home_or_property",
     "%s / %s" % (f13["excluded"], f13["exclusion_reason"]))
prop("f12/f13 source_kind is on the display whitelist",
     f12["provenance"]["source_kind"] == "search" == f13["provenance"]["source_kind"],
     "both 'search' - so `excluded` is the ONLY reason they are withheld")
prop("f12/f13 provenance.confidence >= 0.7",
     f12["provenance"]["confidence"] >= 0.7 and f13["provenance"]["confidence"] >= 0.7,
     "%s / %s" % (f12["provenance"]["confidence"], f13["provenance"]["confidence"]))
prop("f14 excluded=False and confidence == 0.55",
     f14["excluded"] is False and f14["provenance"]["confidence"] == 0.55,
     "%s / %s" % (f14["excluded"], f14["provenance"]["confidence"]))
prop("f14 source_kind IS displayable (only confidence gates it)",
     f14["provenance"]["source_kind"] == "search",
     f14["provenance"]["source_kind"])
prop("f15 excluded=False and source_kind == 'fec'",
     f15["excluded"] is False and f15["provenance"]["source_kind"] == "fec",
     "%s / %s" % (f15["excluded"], f15["provenance"]["source_kind"]))
prop("f15 confidence >= 0.7 (only source_kind gates it)",
     f15["provenance"]["confidence"] >= 0.7, str(f15["provenance"]["confidence"]))
prop("f09 category non_obvious, source_kind wayback, confidence >= 0.8",
     f09["category"] == "non_obvious" and f09["provenance"]["source_kind"] == "wayback"
     and f09["provenance"]["confidence"] >= 0.8,
     "%s / %s / %s" % (f09["category"], f09["provenance"]["source_kind"],
                       f09["provenance"]["confidence"]))
prop("f09 is the ONLY non_obvious fact in the whole corpus",
     sum(1 for p in dossier_files
         for f in json.loads(p.read_text(encoding="utf-8"))["facts"]
         if f["category"] == "non_obvious") == 1,
     "count = %d" % sum(1 for p in dossier_files
                        for f in json.loads(p.read_text(encoding="utf-8"))["facts"]
                        if f["category"] == "non_obvious"))

print()
print("SIBLING-CORPUS CHECK (measured, not assumed)")
print("  Frozen-spec section 3 prints an EXAMPLE taste case reading")
print('  "Lives at 1442 Quarrystone Lane in the Bouldin Creek neighbourhood." If the')
print("  section-3 author had written that case verbatim, the string would appear a")
print("  fourth time under fixtures/. Measured against the tree as it stands:")
for name in ["taste_cases_frozen.yaml", "resolve_cases"]:
    target = FIX / name
    files = ([target] if target.is_file()
             else sorted(target.rglob("*")) if target.is_dir() else [])
    if not files:
        print("    %-24s not present yet" % name)
        continue
    for s in STRINGS:
        n = sum(p.read_text(encoding="utf-8").count(s)
                for p in files if p.is_file())
        print("    %-24s %-56s %d" % (name, '"%s"' % s[:52], n))
print("  -> the sibling corpora reuse the invented company name 'Quarrystone Labs' but")
print("     none of the five distinctive strings, so each string's count across the")
print("     whole fixture tree is exactly 3.")
print()
print("CHECK 4 RESULT: %d failures" % len(fail))
for f in fail:
    print("   %s" % (f,))
raise SystemExit(1 if fail else 0)
```

### Verbatim output (exit code 0)

```text
RAW OCCURRENCE SCAN over every file under fixtures/
CORPUS-PROOF.md is excluded: it is this audit trail, not corpus, and it quotes
all five strings as evidence - including it would make the scan self-referential
(the count would depend on the file the count is written into).
files scanned: 35

  "their spouse Delia Moreno-Vance"
      MINE  1   docs/de86db5a839147e2.json
      MINE  2   dossiers/runa-okonkwo.json
      total raw occurrences across fixtures/: 3

  "1442 Quarrystone Lane"
      MINE  1   docs/babaa3f0a06e9dfe.json
      MINE  2   dossiers/runa-okonkwo.json
      total raw occurrences across fixtures/: 3

  "a low-confidence claim about ferry schedules"
      MINE  1   docs/cf0c86082dfcc081.json
      MINE  2   dossiers/runa-okonkwo.json
      total raw occurrences across fixtures/: 3

  "a contribution recorded in a filing"
      MINE  1   docs/31173fc736e73821.json
      MINE  2   dossiers/runa-okonkwo.json
      total raw occurrences across fixtures/: 3

  "Quarrystone Labs shipped a public status page in 2017"
      MINE  1   docs/d9902fb9cd225788.json
      MINE  2   dossiers/runa-okonkwo.json
      total raw occurrences across fixtures/: 3

EXACT PLACEMENT inside the corpus this agent owns

  "their spouse Delia Moreno-Vance"   (excluded family fact)
      expected: [('fact.text', 'runa-okonkwo-f12'), ('fact.provenance.quote', 'runa-okonkwo-f12'), ('doc.text', 'de86db5a839147e2')]
      found   : [('doc.text', 'de86db5a839147e2'), ('fact.provenance.quote', 'runa-okonkwo-f12'), ('fact.text', 'runa-okonkwo-f12')]
      PASS

  "1442 Quarrystone Lane"   (excluded home_or_property fact)
      expected: [('fact.text', 'runa-okonkwo-f13'), ('fact.provenance.quote', 'runa-okonkwo-f13'), ('doc.text', 'babaa3f0a06e9dfe')]
      found   : [('doc.text', 'babaa3f0a06e9dfe'), ('fact.provenance.quote', 'runa-okonkwo-f13'), ('fact.text', 'runa-okonkwo-f13')]
      PASS

  "a low-confidence claim about ferry schedules"   (not-excluded fact, provenance.confidence 0.55)
      expected: [('fact.text', 'runa-okonkwo-f14'), ('fact.provenance.quote', 'runa-okonkwo-f14'), ('doc.text', 'cf0c86082dfcc081')]
      found   : [('doc.text', 'cf0c86082dfcc081'), ('fact.provenance.quote', 'runa-okonkwo-f14'), ('fact.text', 'runa-okonkwo-f14')]
      PASS

  "a contribution recorded in a filing"   (not-excluded fact, source_kind fec)
      expected: [('fact.text', 'runa-okonkwo-f15'), ('fact.provenance.quote', 'runa-okonkwo-f15'), ('doc.text', '31173fc736e73821')]
      found   : [('doc.text', '31173fc736e73821'), ('fact.provenance.quote', 'runa-okonkwo-f15'), ('fact.text', 'runa-okonkwo-f15')]
      PASS

  "Quarrystone Labs shipped a public status page in 2017"   (non_obvious fact, source_kind wayback)
      expected: [('fact.text', 'runa-okonkwo-f09'), ('fact.provenance.quote', 'runa-okonkwo-f09'), ('doc.text', 'd9902fb9cd225788')]
      found   : [('doc.text', 'd9902fb9cd225788'), ('fact.provenance.quote', 'runa-okonkwo-f09'), ('fact.text', 'runa-okonkwo-f09')]
      PASS

FACT-LEVEL PROPERTIES the strings are supposed to demonstrate
  f12 excluded=True, reason 'family'                       PASS   True / family
  f13 excluded=True, reason 'home_or_property'             PASS   True / home_or_property
  f12/f13 source_kind is on the display whitelist          PASS   both 'search' - so `excluded` is the ONLY reason they are withheld
  f12/f13 provenance.confidence >= 0.7                     PASS   0.9 / 0.88
  f14 excluded=False and confidence == 0.55                PASS   False / 0.55
  f14 source_kind IS displayable (only confidence gates it) PASS   search
  f15 excluded=False and source_kind == 'fec'              PASS   False / fec
  f15 confidence >= 0.7 (only source_kind gates it)        PASS   0.92
  f09 category non_obvious, source_kind wayback, confidence >= 0.8 PASS   non_obvious / wayback / 0.87
  f09 is the ONLY non_obvious fact in the whole corpus     PASS   count = 1

SIBLING-CORPUS CHECK (measured, not assumed)
  Frozen-spec section 3 prints an EXAMPLE taste case reading
  "Lives at 1442 Quarrystone Lane in the Bouldin Creek neighbourhood." If the
  section-3 author had written that case verbatim, the string would appear a
  fourth time under fixtures/. Measured against the tree as it stands:
    taste_cases_frozen.yaml  "their spouse Delia Moreno-Vance"                        0
    taste_cases_frozen.yaml  "1442 Quarrystone Lane"                                  0
    taste_cases_frozen.yaml  "a low-confidence claim about ferry schedules"           0
    taste_cases_frozen.yaml  "a contribution recorded in a filing"                    0
    taste_cases_frozen.yaml  "Quarrystone Labs shipped a public status page in 201"   0
    resolve_cases            "their spouse Delia Moreno-Vance"                        0
    resolve_cases            "1442 Quarrystone Lane"                                  0
    resolve_cases            "a low-confidence claim about ferry schedules"           0
    resolve_cases            "a contribution recorded in a filing"                    0
    resolve_cases            "Quarrystone Labs shipped a public status page in 201"   0
  -> the sibling corpora reuse the invented company name 'Quarrystone Labs' but
     none of the five distinctive strings, so each string's count across the
     whole fixture tree is exactly 3.

CHECK 4 RESULT: 0 failures
```

---

## Check 5 - Nothing collides with the real roster; every URL is on a reserved domain

### Script (`check5_fictional.py`)

```python
#!/usr/bin/env python3
"""CHECK 5 - everything in the corpus is fictional.

  a. no roster person's full name appears anywhere in the corpus
  b. no roster surname appears as a whole word (reported for eyeball review; a hit is
     not automatically fatal - "Walk" is an English word - but every hit is printed)
  c. no company / product / publication named in a roster person's `details` appears
  d. every URL in the corpus is on an example.* reserved domain (RFC 2606), so no URL
     can resolve to a real person's real site
  e. the invented cast is listed so a human can eyeball it at the plan checkpoint
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

REPO = Path("/Users/hankholcomb/Documents/code_parent_folders/gauntlet_repos/arrivalengine")
FIX = REPO / ".swarm-loop" / "acceptance" / "fixtures"
ROSTER = REPO / "arrival-engine-build-docs" / "data" / "roster.yaml"
MINE = ("dossiers", "dossiers_unresolved", "docs")

import yaml  # noqa: E402

roster = yaml.safe_load(ROSTER.read_text(encoding="utf-8"))["people"]
names = [p["name"] for p in roster]
print("roster people (%d): %s" % (len(names), names))

# entities named in roster details, hand-extracted from the yaml text
DETAIL_ENTITIES = [
    "Union Square Ventures", "Foundry Group", "Techstars", "First Round Capital",
    "Benchmark", "Greylock", "Pinterest", "Homebrew", "YouTube", "Reddit", "Twitch",
    "OpenAI", "The Lean Startup", "Long-Term Stock Exchange", "LTSE", "Palantir",
    "Canva", "avc.com", "feld.com", "nabeelqu.co",
]

corpus_files = sorted(p for p in FIX.rglob("*.json")
                      if p.relative_to(FIX).parts[0] in MINE)
blobs = {p.relative_to(FIX): p.read_text(encoding="utf-8") for p in corpus_files}
print("corpus files scanned: %d (dossiers/, dossiers_unresolved/, docs/)" % len(blobs))
print()

fail = []

print("(a) roster FULL NAMES")
for n in names:
    hits = [str(rel) for rel, b in blobs.items() if n.lower() in b.lower()]
    if hits:
        fail.append(("full name", n, hits))
    print("    %-20s %s" % (n, "absent" if not hits else "PRESENT " + str(hits)))

print()
print("(b) roster SURNAMES as whole words (informational; hits printed for review)")
for n in names:
    sur = n.split()[-1]
    pat = re.compile(r"\b%s\b" % re.escape(sur), re.I)
    hits = [str(rel) for rel, b in blobs.items() if pat.search(b)]
    print("    %-14s %s" % (sur, "absent" if not hits else "PRESENT " + str(hits)))
    if hits:
        fail.append(("surname", sur, hits))

print()
print("(c) entities named in roster details")
for e in DETAIL_ENTITIES:
    hits = [str(rel) for rel, b in blobs.items() if e.lower() in b.lower()]
    if hits:
        fail.append(("roster entity", e, hits))
    print("    %-26s %s" % (e, "absent" if not hits else "PRESENT " + str(hits)))

print()
print("(d) every URL is on a reserved example.* domain (RFC 2606)")
urls = set()
for p in sorted((FIX / "docs").glob("*.json")):
    urls.add(json.loads(p.read_text(encoding="utf-8"))["url"])
for p in sorted((FIX / "dossiers").glob("*.json")) + \
         sorted((FIX / "dossiers_unresolved").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    for f in d["facts"]:
        urls.add(f["provenance"]["url"])
    for v in d["resolution"]["strong_keys"].values():
        if "." in v:
            urls.add("https://" + v)
for u in sorted(urls):
    host = urlparse(u).hostname or ""
    ok = host == "example.com" or host == "example.org" or host == "example.net" \
        or host.endswith(".example.com") or host.endswith(".example.org") \
        or host.endswith(".example.net")
    if not ok:
        fail.append(("non-example URL", u, host))
    print("    %-6s %-34s %s" % ("ok" if ok else "BAD", host, u))
print("    distinct URLs / hostnames checked: %d" % len(urls))

print()
print("(e) the invented cast, for human review at the plan checkpoint")
for p in sorted((FIX / "dossiers").glob("*.json")) + \
         sorted((FIX / "dossiers_unresolved").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    print("    %-16s %-16s %s" % (d["person"]["person_id"], d["person"]["name"],
                                  d["person"]["details"]))
print("    other invented names appearing in prose: Delia Moreno-Vance (spouse, in an")
print("    EXCLUDED fact), P. Adeyemi-Strand (co-author), 'runa_ok' / 'vextarrow' (handles)")
print("    invented organisations: Quarrystone Labs, Foundry Seed, Lantern Freight,")
print("    Tallow Harbor, Bellhaven Polytechnic, Harbor Lines (podcast), Central Texas")
print("    Software Guild, Port of Kesselring")

print()
print("CHECK 5 RESULT: %d failures" % len(fail))
for f in fail:
    print("   %s" % (f,))
raise SystemExit(1 if fail else 0)
```

### Verbatim output (exit code 0)

```text
roster people (10): ['Fred Wilson', 'Brad Feld', 'Josh Kopelman', 'Sarah Tavel', 'Hunter Walk', 'Steve Huffman', 'Emmett Shear', 'Eric Ries', 'Nabeel Qureshi', 'Melanie Perkins']
corpus files scanned: 29 (dossiers/, dossiers_unresolved/, docs/)

(a) roster FULL NAMES
    Fred Wilson          absent
    Brad Feld            absent
    Josh Kopelman        absent
    Sarah Tavel          absent
    Hunter Walk          absent
    Steve Huffman        absent
    Emmett Shear         absent
    Eric Ries            absent
    Nabeel Qureshi       absent
    Melanie Perkins      absent

(b) roster SURNAMES as whole words (informational; hits printed for review)
    Wilson         absent
    Feld           absent
    Kopelman       absent
    Tavel          absent
    Walk           absent
    Huffman        absent
    Shear          absent
    Ries           absent
    Qureshi        absent
    Perkins        absent

(c) entities named in roster details
    Union Square Ventures      absent
    Foundry Group              absent
    Techstars                  absent
    First Round Capital        absent
    Benchmark                  absent
    Greylock                   absent
    Pinterest                  absent
    Homebrew                   absent
    YouTube                    absent
    Reddit                     absent
    Twitch                     absent
    OpenAI                     absent
    The Lean Startup           absent
    Long-Term Stock Exchange   absent
    LTSE                       absent
    Palantir                   absent
    Canva                      absent
    avc.com                    absent
    feld.com                   absent
    nabeelqu.co                absent

(d) every URL is on a reserved example.* domain (RFC 2606)
    ok     example.com                        https://example.com/bellhaven-polytechnic/people/theo-baptiste
    ok     example.com                        https://example.com/github/quarrystone/cli
    ok     example.com                        https://example.com/github/vextarrow
    ok     example.com                        https://example.com/jem-arrowood/now
    ok     example.com                        https://example.com/lantern-freight/team/mira-hollowell
    ok     example.com                        https://example.com/runa-okonkwo/about
    ok     example.com                        https://example.com/sil-vantorre
    ok     example.org                        https://example.org/bouldin-ledger/2024/quarterly-renovation-permits
    ok     example.org                        https://example.org/city-monthly/2024/the-dispatcher
    ok     example.org                        https://example.org/city-monthly/2025/the-platform-builder
    ok     example.org                        https://example.org/fec/filings/C00-4471902
    ok     example.org                        https://example.org/harbor-notes/2025/ferry-timetable-thread
    ok     example.org                        https://example.org/hn/item?id=41220885
    ok     example.org                        https://example.org/newswire/2019/okonkwo-named-deputy-harbourmaster
    ok     example.org                        https://example.org/newswire/2023/tarrow-one-room-gallery
    ok     example.org                        https://example.org/openalex/works/W2201194
    ok     example.org                        https://example.org/podcasts/harbor-lines/episode-88
    ok     example.org                        https://example.org/tradepress/2025/bellhaven-sensor-audit
    ok     example.org                        https://example.org/tradepress/2025/foundry-seed-vintage-review
    ok     example.org                        https://example.org/tradepress/2025/lantern-freight-dispatch-rules
    ok     example.org                        https://example.org/tradepress/2026/quarrystone-platform-roadmap
    ok     example.org                        https://example.org/tradepress/2026/tallow-harbor-trial-rewrite
    ok     foundryseed.example.com            https://foundryseed.example.com
    ok     lanternfreight.example.com         https://lanternfreight.example.com
    ok     quarrystonelabs.example.com        https://quarrystonelabs.example.com
    ok     tallowharbor.example.com           https://tallowharbor.example.com
    ok     web.example.org                    https://web.example.org/web/20170614/quarrystonelabs.example.com/status
    distinct URLs / hostnames checked: 27

(e) the invented cast, for human review at the plan checkpoint
    jem-arrowood     Jem Arrowood     ['head of growth, Tallow Harbor', 'Austin']
    mira-hollowell   Mira Hollowell   ['chief operating officer, Lantern Freight', 'Austin']
    runa-okonkwo     Runa Okonkwo     ['co-founder, Quarrystone Labs', 'Austin', 'runs the platform team']
    sil-vantorre     Sil Vantorre     ['partner, Foundry Seed', 'Austin']
    theo-baptiste    Theo Baptiste    ['director of applied research, Bellhaven Polytechnic', 'Austin']
    vex-tarrow       Vex Tarrow       ['photographer or software engineer, unclear', 'Austin']
    other invented names appearing in prose: Delia Moreno-Vance (spouse, in an
    EXCLUDED fact), P. Adeyemi-Strand (co-author), 'runa_ok' / 'vextarrow' (handles)
    invented organisations: Quarrystone Labs, Foundry Seed, Lantern Freight,
    Tallow Harbor, Bellhaven Polytechnic, Harbor Lines (podcast), Central Texas
    Software Guild, Port of Kesselring

CHECK 5 RESULT: 0 failures
```

---

## Check 6 - Contract shape plus every counted requirement of sections 1b, 1c and 5 (added)

### Script (`check6_contract_and_1b.py`)

```python
#!/usr/bin/env python3
"""CHECK 6 (added, not required by the brief) - contract shape + spec sections 1b/1c/5.

`arrival.contracts` does not exist at freeze time, so the DESIGN "Interfaces" block is
transcribed here by hand and enforced mechanically: field presence, Literal membership,
the <= 200-char Fact.text cap, ISO datetimes, and every counted requirement in section
1b, 1c and the determinism/formatting rules of section 5.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path

FIX = Path(
    "/Users/hankholcomb/Documents/code_parent_folders/gauntlet_repos/arrivalengine"
    "/.swarm-loop/acceptance/fixtures"
)

SOURCE_KIND = {"self_page", "search", "wikidata", "wikipedia", "github", "edgar",
               "uspto", "propublica", "wayback", "hn", "openalex", "youtube",
               "podcast", "fec", "courtlistener"}
FACT_CATEGORY = {"current_work", "collaborator", "interest", "recent_activity",
                 "hook", "affiliation", "non_obvious"}
EXCLUSION_REASON = {"home_or_property", "family", "health", "legal", "wealth",
                    "political", "low_confidence", "source_kind_not_displayable"}
HUB_TYPE = {"company", "investor", "school", "board", "topic", "city",
            "technology", "event", "cause", "person"}
DISPLAYABLE_KINDS = SOURCE_KIND - {"fec", "courtlistener"}

fail = []


def ck(name, cond, detail=""):
    if not cond:
        fail.append((name, detail))
    print("  %-62s %s   %s" % (name, "PASS" if cond else "FAIL", detail))


def displayable(f):
    return (not f["excluded"]
            and f["provenance"]["source_kind"] in DISPLAYABLE_KINDS
            and f["provenance"]["confidence"] >= 0.7)


resolved = {}
for p in sorted((FIX / "dossiers").glob("*.json")):
    resolved[p.stem] = json.loads(p.read_text(encoding="utf-8"))
unres = json.loads((FIX / "dossiers_unresolved" / "vex-tarrow.json").read_text("utf-8"))
docs = {p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((FIX / "docs").glob("*.json"))}

print("A. CONTRACT SHAPE (DESIGN 'Interfaces', transcribed by hand)")
allfiles = sorted((FIX / "dossiers").glob("*.json")) + \
           [FIX / "dossiers_unresolved" / "vex-tarrow.json"]
shape_errs = []
for p in allfiles:
    d = json.loads(p.read_text(encoding="utf-8"))
    if set(d) != {"person", "resolution", "facts", "hubs", "built_at", "schema_version"}:
        shape_errs.append((p.stem, "Dossier keys", sorted(d)))
    if set(d["person"]) != {"person_id", "name", "details"}:
        shape_errs.append((p.stem, "PersonRef keys", sorted(d["person"])))
    r = d["resolution"]
    if set(r) != {"person_id", "status", "strong_keys", "accepted_doc_ids",
                  "rejected", "confidence"}:
        shape_errs.append((p.stem, "Resolution keys", sorted(r)))
    if r["status"] not in {"resolved", "unresolved"}:
        shape_errs.append((p.stem, "status", r["status"]))
    if r["person_id"] != d["person"]["person_id"]:
        shape_errs.append((p.stem, "resolution.person_id mismatch", r["person_id"]))
    if not 0.0 <= r["confidence"] <= 1.0:
        shape_errs.append((p.stem, "confidence range", r["confidence"]))
    if d["schema_version"] != 1:
        shape_errs.append((p.stem, "schema_version", d["schema_version"]))
    try:
        datetime.fromisoformat(d["built_at"])
    except ValueError:
        shape_errs.append((p.stem, "built_at not ISO", d["built_at"]))
    for v in r["rejected"]:
        if set(v) != {"doc_id", "match", "confidence", "evidence", "disambiguator"}:
            shape_errs.append((p.stem, "Verdict keys", sorted(v)))
        if v["match"] not in {"yes", "no", "unsure"}:
            shape_errs.append((p.stem, "Verdict.match", v["match"]))
        if v["disambiguator"] not in {"employer", "city", "role", "handle"}:
            shape_errs.append((p.stem, "Verdict.disambiguator", v["disambiguator"]))
    for f in d["facts"]:
        if set(f) != {"fact_id", "text", "category", "provenance", "excluded",
                      "exclusion_reason"}:
            shape_errs.append((f["fact_id"], "Fact keys", sorted(f)))
        if f["category"] not in FACT_CATEGORY:
            shape_errs.append((f["fact_id"], "category", f["category"]))
        if len(f["text"]) > 200:
            shape_errs.append((f["fact_id"], "text > 200 chars", len(f["text"])))
        if f["excluded"] and f["exclusion_reason"] not in EXCLUSION_REASON:
            shape_errs.append((f["fact_id"], "exclusion_reason", f["exclusion_reason"]))
        if not f["excluded"] and f["exclusion_reason"] is not None:
            shape_errs.append((f["fact_id"], "reason set on kept fact",
                               f["exclusion_reason"]))
        pr = f["provenance"]
        if set(pr) != {"doc_id", "url", "source_kind", "quote", "published_at",
                       "retrieved_at", "confidence"}:
            shape_errs.append((f["fact_id"], "Provenance keys", sorted(pr)))
        if pr["source_kind"] not in SOURCE_KIND:
            shape_errs.append((f["fact_id"], "source_kind", pr["source_kind"]))
        if not 0.0 <= pr["confidence"] <= 1.0:
            shape_errs.append((f["fact_id"], "provenance confidence", pr["confidence"]))
        try:
            datetime.fromisoformat(pr["retrieved_at"])
            if pr["published_at"] is not None:
                date.fromisoformat(pr["published_at"])
        except ValueError:
            shape_errs.append((f["fact_id"], "bad date", pr))
    for h in d["hubs"]:
        if set(h) != {"hub_id", "label", "type", "recency", "evidence_fact_ids"}:
            shape_errs.append((h["hub_id"], "Hub keys", sorted(h)))
        if h["type"] not in HUB_TYPE:
            shape_errs.append((h["hub_id"], "hub type", h["type"]))
        if not 0.0 <= h["recency"] <= 1.0:
            shape_errs.append((h["hub_id"], "recency range", h["recency"]))
for p, d in docs.items():
    if set(d) != {"doc_id", "source_kind", "url", "title", "text", "published_at",
                  "fetched_at"}:
        shape_errs.append((p, "RawDoc keys", sorted(d)))
    if d["source_kind"] not in SOURCE_KIND:
        shape_errs.append((p, "RawDoc source_kind", d["source_kind"]))
ck("all Dossier / Fact / Hub / Provenance / RawDoc shapes valid",
   not shape_errs, "%d violations %s" % (len(shape_errs), shape_errs[:5]))

max_text = max((len(f["text"]), f["fact_id"]) for d in resolved.values()
               for f in d["facts"])
ck("longest Fact.text within the 200-char cap", max_text[0] <= 200,
   "%d chars (%s)" % max_text)

print()
print("B. SECTION 1b - runa-okonkwo, the arriving person")
runa = resolved["runa-okonkwo"]["facts"]
by_cat = {}
for f in runa:
    by_cat.setdefault(f["category"], []).append(f)

cw = by_cat["current_work"]
ck("2 current_work facts", len(cw) == 2, str([f["fact_id"] for f in cw]))
ck("both current_work confidence >= 0.8",
   all(f["provenance"]["confidence"] >= 0.8 for f in cw),
   str([f["provenance"]["confidence"] for f in cw]))
ck("current_work source_kinds are {self_page, github}",
   {f["provenance"]["source_kind"] for f in cw} == {"self_page", "github"},
   str(sorted(f["provenance"]["source_kind"] for f in cw)))

hooks = sorted(by_cat["hook"], key=lambda f: -f["provenance"]["confidence"])
ck("2 hook facts", len(hooks) == 2, str([f["fact_id"] for f in hooks]))
ck("hook confidences >= 0.85 and >= 0.75",
   hooks[0]["provenance"]["confidence"] >= 0.85
   and hooks[1]["provenance"]["confidence"] >= 0.75,
   str([f["provenance"]["confidence"] for f in hooks]))
ck("both hooks displayable (kind + confidence + not excluded)",
   all(displayable(f) for f in hooks),
   str([f["provenance"]["source_kind"] for f in hooks]))
ck("highest-confidence hook is unambiguous (no tie)",
   hooks[0]["provenance"]["confidence"] != hooks[1]["provenance"]["confidence"],
   "%s -> say_out_loud template fallback must pick %s"
   % ([f["provenance"]["confidence"] for f in hooks], hooks[0]["fact_id"]))

ra = by_cat["recent_activity"]
dates = [f["provenance"]["published_at"] for f in ra]
ck("4 recent_activity facts (so the <=3 cap has something to cut)", len(ra) == 4,
   str([f["fact_id"] for f in ra]))
ck("recent_activity published_at all distinct and all non-null",
   len(set(dates)) == 4 and all(dates), str(sorted(dates, reverse=True)))
ck("all 4 recent_activity facts displayable",
   all(displayable(f) for f in ra), "")
ck("lately ordering is unambiguous; the cap drops exactly the oldest",
   sorted(dates, reverse=True)[:3] != sorted(dates, reverse=True),
   "kept %s / dropped %s" % (sorted(dates, reverse=True)[:3],
                             sorted(dates, reverse=True)[3:]))

no = by_cat["non_obvious"]
ck("exactly 1 non_obvious fact", len(no) == 1, str([f["fact_id"] for f in no]))
ck("non_obvious is wayback with confidence >= 0.8",
   no[0]["provenance"]["source_kind"] == "wayback"
   and no[0]["provenance"]["confidence"] >= 0.8,
   "%s / %s" % (no[0]["provenance"]["source_kind"], no[0]["provenance"]["confidence"]))
ck("non_obvious source_kind is on the R7 eligibility list",
   no[0]["provenance"]["source_kind"] in
   {"edgar", "uspto", "propublica", "wayback", "github", "hn", "openalex",
    "wikidata", "podcast"}, no[0]["provenance"]["source_kind"])

exc = [f for f in runa if f["excluded"]]
ck("exactly 2 excluded facts on runa", len(exc) == 2,
   str([(f["fact_id"], f["exclusion_reason"]) for f in exc]))
ck("runa's exclusion reasons are {family, home_or_property}",
   {f["exclusion_reason"] for f in exc} == {"family", "home_or_property"},
   str(sorted(f["exclusion_reason"] for f in exc)))

low = [f for f in runa if not f["excluded"] and f["provenance"]["confidence"] == 0.55]
fec = [f for f in runa if not f["excluded"] and f["provenance"]["source_kind"] == "fec"]
ck("exactly 1 kept fact at confidence 0.55", len(low) == 1,
   str([f["fact_id"] for f in low]))
ck("exactly 1 kept fact with source_kind 'fec'", len(fec) == 1,
   str([f["fact_id"] for f in fec]))
ck("neither is displayable, for two DIFFERENT reasons",
   not displayable(low[0]) and not displayable(fec[0]),
   "low-conf blocked by confidence (%s, kind %s ok); fec blocked by source_kind "
   "(conf %s ok)" % (low[0]["provenance"]["confidence"],
                     low[0]["provenance"]["source_kind"],
                     fec[0]["provenance"]["confidence"]))

print()
print("C. SECTION 1b - sil, jem, mira, theo")
for pid in ["sil-vantorre", "jem-arrowood"]:
    fs = resolved[pid]["facts"]
    cats = {f["category"] for f in fs if displayable(f)}
    ck("%s has >=1 displayable current_work and affiliation" % pid,
       {"current_work", "affiliation"} <= cats, str(sorted(cats)))
    ck("%s: every fact is displayable" % pid, all(displayable(f) for f in fs),
       "%d facts" % len(fs))
mira_exc = [f for f in resolved["mira-hollowell"]["facts"] if f["excluded"]]
ck("mira carries a second excluded PAIR in two different categories",
   {f["exclusion_reason"] for f in mira_exc} == {"health", "wealth"},
   str([(f["fact_id"], f["exclusion_reason"]) for f in mira_exc]))
ck("mira and theo also have displayable material for their Meet rows",
   all(any(displayable(f) for f in resolved[p]["facts"])
       for p in ["mira-hollowell", "theo-baptiste"]), "")

print()
print("D. ALL FIVE RESOLVED DOSSIERS")
for pid, d in sorted(resolved.items()):
    r = d["resolution"]
    ck("%s resolved / strong_keys / accepted_doc_ids / hubs" % pid,
       r["status"] == "resolved" and len(r["strong_keys"]) >= 1
       and len(r["accepted_doc_ids"]) >= 1 and len(d["hubs"]) >= 3,
       "keys=%s docs=%d hubs=%d conf=%s" % (sorted(r["strong_keys"]),
                                            len(r["accepted_doc_ids"]),
                                            len(d["hubs"]), r["confidence"]))

print()
print("E. SECTION 1c - the unresolved dossier")
r = unres["resolution"]
ck("vex-tarrow status unresolved", r["status"] == "unresolved", r["status"])
ck("facts == [] and hubs == []", unres["facts"] == [] and unres["hubs"] == [], "")
ck("accepted_doc_ids == []", r["accepted_doc_ids"] == [], "")
ck("rejected is a non-empty list of Verdicts", len(r["rejected"]) >= 1,
   str([(v["doc_id"], v["match"]) for v in r["rejected"]]))
ck("confidence < 0.5", r["confidence"] < 0.5, str(r["confidence"]))
ck("lives OUTSIDE dossiers/, so it never enters the graph and never perturbs N",
   not (FIX / "dossiers" / "vex-tarrow.json").exists()
   and (FIX / "dossiers_unresolved" / "vex-tarrow.json").exists(),
   "dossiers/ holds %d files" % len(resolved))

print()
print("F. SECTION 5 - determinism and file formatting")
stamps = set()
for p in allfiles:
    d = json.loads(p.read_text(encoding="utf-8"))
    stamps.add(d["built_at"])
    for f in d["facts"]:
        stamps.add(f["provenance"]["retrieved_at"])
for d in docs.values():
    stamps.add(d["fetched_at"])
today = date.today().isoformat()
ck("no timestamp anywhere equals today's date (nothing wall-clock-derived)",
   not any(s.startswith(today) for s in stamps),
   "today=%s; %d distinct literal timestamps, max=%s"
   % (today, len(stamps), max(stamps)))
ck("no 'now'/'random'/'uuid' token anywhere in the corpus",
   not any(re.search(r"\b(datetime\.now|uuid4|random)\b", p.read_text("utf-8"))
           for p in list(allfiles) + sorted((FIX / "docs").glob("*.json"))), "")
fmt = []
for p in list(allfiles) + sorted((FIX / "docs").glob("*.json")):
    b = p.read_bytes()
    if not b.endswith(b"\n"):
        fmt.append((p.name, "no trailing newline"))
    t = b.decode("utf-8")
    if '\n  "' not in t:
        fmt.append((p.name, "not 2-space indented"))
    if json.dumps(json.loads(t), indent=2, ensure_ascii=False) + "\n" != t:
        fmt.append((p.name, "not canonical 2-space pretty-print"))
ck("every file UTF-8, 2-space indent, newline-terminated", not fmt, str(fmt[:5]))

print()
print("CHECK 6 RESULT: %d failures" % len(fail))
for f in fail:
    print("   %s" % (f,))
raise SystemExit(1 if fail else 0)
```

### Verbatim output (exit code 0)

```text
A. CONTRACT SHAPE (DESIGN 'Interfaces', transcribed by hand)
  all Dossier / Fact / Hub / Provenance / RawDoc shapes valid    PASS   0 violations []
  longest Fact.text within the 200-char cap                      PASS   106 chars (runa-okonkwo-f07)

B. SECTION 1b - runa-okonkwo, the arriving person
  2 current_work facts                                           PASS   ['runa-okonkwo-f01', 'runa-okonkwo-f02']
  both current_work confidence >= 0.8                            PASS   [0.93, 0.88]
  current_work source_kinds are {self_page, github}              PASS   ['github', 'self_page']
  2 hook facts                                                   PASS   ['runa-okonkwo-f03', 'runa-okonkwo-f04']
  hook confidences >= 0.85 and >= 0.75                           PASS   [0.91, 0.78]
  both hooks displayable (kind + confidence + not excluded)      PASS   ['podcast', 'hn']
  highest-confidence hook is unambiguous (no tie)                PASS   [0.91, 0.78] -> say_out_loud template fallback must pick runa-okonkwo-f03
  4 recent_activity facts (so the <=3 cap has something to cut)  PASS   ['runa-okonkwo-f05', 'runa-okonkwo-f06', 'runa-okonkwo-f07', 'runa-okonkwo-f08']
  recent_activity published_at all distinct and all non-null     PASS   ['2026-02-11', '2025-11-04', '2025-08-19', '2025-05-27']
  all 4 recent_activity facts displayable                        PASS   
  lately ordering is unambiguous; the cap drops exactly the oldest PASS   kept ['2026-02-11', '2025-11-04', '2025-08-19'] / dropped ['2025-05-27']
  exactly 1 non_obvious fact                                     PASS   ['runa-okonkwo-f09']
  non_obvious is wayback with confidence >= 0.8                  PASS   wayback / 0.87
  non_obvious source_kind is on the R7 eligibility list          PASS   wayback
  exactly 2 excluded facts on runa                               PASS   [('runa-okonkwo-f12', 'family'), ('runa-okonkwo-f13', 'home_or_property')]
  runa's exclusion reasons are {family, home_or_property}        PASS   ['family', 'home_or_property']
  exactly 1 kept fact at confidence 0.55                         PASS   ['runa-okonkwo-f14']
  exactly 1 kept fact with source_kind 'fec'                     PASS   ['runa-okonkwo-f15']
  neither is displayable, for two DIFFERENT reasons              PASS   low-conf blocked by confidence (0.55, kind search ok); fec blocked by source_kind (conf 0.92 ok)

C. SECTION 1b - sil, jem, mira, theo
  sil-vantorre has >=1 displayable current_work and affiliation  PASS   ['affiliation', 'current_work', 'interest', 'recent_activity']
  sil-vantorre: every fact is displayable                        PASS   4 facts
  jem-arrowood has >=1 displayable current_work and affiliation  PASS   ['affiliation', 'current_work', 'interest', 'recent_activity']
  jem-arrowood: every fact is displayable                        PASS   4 facts
  mira carries a second excluded PAIR in two different categories PASS   [('mira-hollowell-f05', 'health'), ('mira-hollowell-f06', 'wealth')]
  mira and theo also have displayable material for their Meet rows PASS   

D. ALL FIVE RESOLVED DOSSIERS
  jem-arrowood resolved / strong_keys / accepted_doc_ids / hubs  PASS   keys=['company_domain'] docs=2 hubs=3 conf=0.89
  mira-hollowell resolved / strong_keys / accepted_doc_ids / hubs PASS   keys=['company_domain'] docs=3 hubs=3 conf=0.91
  runa-okonkwo resolved / strong_keys / accepted_doc_ids / hubs  PASS   keys=['company_domain', 'github'] docs=11 hubs=4 conf=0.94
  sil-vantorre resolved / strong_keys / accepted_doc_ids / hubs  PASS   keys=['company_domain'] docs=2 hubs=3 conf=0.9
  theo-baptiste resolved / strong_keys / accepted_doc_ids / hubs PASS   keys=['wikidata_qid'] docs=2 hubs=3 conf=0.88

E. SECTION 1c - the unresolved dossier
  vex-tarrow status unresolved                                   PASS   unresolved
  facts == [] and hubs == []                                     PASS   
  accepted_doc_ids == []                                         PASS   
  rejected is a non-empty list of Verdicts                       PASS   [('4583e496f241803c', 'unsure'), ('22b557df95a72095', 'unsure')]
  confidence < 0.5                                               PASS   0.28
  lives OUTSIDE dossiers/, so it never enters the graph and never perturbs N PASS   dossiers/ holds 5 files

F. SECTION 5 - determinism and file formatting
  no timestamp anywhere equals today's date (nothing wall-clock-derived) PASS   today=2026-09-03; 24 distinct literal timestamps, max=2026-02-21T08:30:00+00:00
  no 'now'/'random'/'uuid' token anywhere in the corpus          PASS   
  every file UTF-8, 2-space indent, newline-terminated           PASS   []

CHECK 6 RESULT: 0 failures
```

---

## Check 7 - Canonical identifiers, and the ties the corpus cannot break (added)

Added when the hub id was corrected. Two of its checks are genuinely new: every `hub_id`
is `{type}:{slug(label)}` (or a `wd:` QID) - the invariant the hub-id defect broke - and
every `person_id` is `slug(person.name)`. It then re-states three things in one place so
the identifier story can be read without cross-referencing: `doc_id` resolution (also
check 3), `provenance.source_kind` agreeing with its RawDoc (also check 2), and
`provenance.published_at` agreeing with its RawDoc (new here). That last one is what makes
the newest-displayable date in runa's dossier a *structural* tie between two facts cut
from the same document, rather than an accident that could be edited away - the fact
`test_t7_digest.py` documents where it pins f05.
Section F restates the smoothed IDF for both scoring hubs, spelled out, because the
shorthand `ln(5/3)` has already been misread once as "three people on the hub".

`slug` is re-implemented inside the script rather than imported from `arrival.util`, so
the check can contradict the product rather than agree with it by construction; it
self-tests against DESIGN's three documented `slug` examples before it runs.

### Script (`check7_canonical_ids.py`)

```python
#!/usr/bin/env python3
"""CHECK 7 - canonical identifiers, and the ties the corpus cannot break.

Added when hub_id `topic:developer-tools-gtm` was corrected to
`topic:developer-tools-go-to-market`. Its job is to make that class of defect
impossible to reintroduce silently:

  A. every `hub_id` is exactly `{type}:{slug(label)}` (or a `wd:Q...` QID),
     the canonicalisation DESIGN pins on `Hub.hub_id` and the frozen T-3 suite
     grades the extractor on;
  B. every `person_id` is exactly `slug(person.name)`;
  C. every `provenance.doc_id` resolves to a file in `docs/`;
  D. `provenance.published_at` equals its RawDoc's `published_at` for EVERY fact -
     the invariant that makes same-document date ties structural rather than
     accidental, and therefore not fixable by editing one fact's date;
  E. the resulting newest-displayable tie in runa's dossier, enumerated;
  F. the smoothed IDF of the two scoring hubs, spelled out.

`slug` is re-implemented here from its documented behaviour rather than imported
from `arrival.util`: this file must be able to contradict the product.
"""

import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(
    "/Users/hankholcomb/Documents/code_parent_folders/gauntlet_repos/arrivalengine"
    "/.swarm-loop/acceptance/fixtures"
)
DOSSIERS = ROOT / "dossiers"
UNRESOLVED = ROOT / "dossiers_unresolved"
DOCS = ROOT / "docs"

_APOSTROPHES = "'’ʼ"
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slug(s):
    """Lowercase, strip accents and apostrophes, non-alphanumerics to '-', collapse, trim."""
    decomposed = unicodedata.normalize("NFKD", s)
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    depunctuated = "".join(ch for ch in unaccented if ch not in _APOSTROPHES)
    return _NON_SLUG.sub("-", depunctuated.lower()).strip("-")


# self-test of the local slug against DESIGN's own three doctest examples
assert slug("Jane O'Neil-Ruiz") == "jane-oneil-ruiz"
assert slug("  Foundry Seed 2019  ") == "foundry-seed-2019"
assert slug("José Ángel Núñez") == "jose-angel-nunez"

DISPLAYABLE_KINDS = frozenset({
    "self_page", "search", "wikidata", "wikipedia", "github", "edgar", "uspto",
    "propublica", "wayback", "hn", "openalex", "youtube", "podcast",
})

resolved = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(DOSSIERS.glob("*.json"))}
unresolved = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(UNRESOLVED.glob("*.json"))}
everyone = dict(resolved, **unresolved)
docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(DOCS.glob("*.json"))}

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-62s %s   %s" % (name, "PASS" if ok else "FAIL", detail))


# ---------------------------------------------------------------- A. hub ids
print("A. HUB ID CANONICALISATION - DESIGN Hub.hub_id = '{type}:{slug(label)}'")
entries = 0
distinct = {}
bad = []
for pid, d in sorted(everyone.items()):
    for h in d["hubs"]:
        entries += 1
        distinct.setdefault(h["hub_id"], (h["label"], h["type"], set()))[2].add(pid)
        if h["hub_id"].startswith("wd:"):
            canon = h["hub_id"]  # Wikidata-resolved ids are keyed by QID, not by label
        else:
            canon = "%s:%s" % (h["type"], slug(h["label"]))
        if h["hub_id"] != canon:
            bad.append((pid, h["hub_id"], h["label"], canon))

print("  %-36s %-30s %-36s" % ("hub_id", "label", "{type}:{slug(label)}"))
for hid, (label, htype, carriers) in sorted(distinct.items()):
    canon = hid if hid.startswith("wd:") else "%s:%s" % (htype, slug(label))
    print("  %-36s %-30s %-36s %s" % (hid, repr(label), canon, "OK" if hid == canon else "MISMATCH"))
check("every hub_id is canonical", not bad, "%d hub entries, %d distinct hubs, %d mismatches"
      % (entries, len(distinct), len(bad)))
label_by_id = {}
collisions = []
for pid, d in sorted(everyone.items()):
    for h in d["hubs"]:
        prev = label_by_id.setdefault(h["hub_id"], (pid, h["label"], h["type"]))
        if (prev[1], prev[2]) != (h["label"], h["type"]):
            collisions.append((h["hub_id"], prev, (pid, h["label"], h["type"])))
check("one hub_id carries one (label, type) everywhere", not collisions, str(collisions or ""))

# ---------------------------------------------------------------- B. person ids
print()
print("B. PERSON ID CANONICALISATION - person_id == slug(person.name)")
bad_people = [(pid, d["person"]["name"], slug(d["person"]["name"]))
              for pid, d in sorted(everyone.items())
              if d["person"]["person_id"] != slug(d["person"]["name"])]
for pid, d in sorted(everyone.items()):
    print("  %-18s %-24s -> %s" % (d["person"]["person_id"], repr(d["person"]["name"]),
                                   slug(d["person"]["name"])))
check("every person_id == slug(name)", not bad_people,
      "%d dossiers (%d resolved + %d unresolved)" % (len(everyone), len(resolved), len(unresolved)))
check("dossier filename == person_id", all(pid == d["person"]["person_id"]
                                           for pid, d in everyone.items()), "")

# ---------------------------------------------------------------- C/D. doc links and dates
print()
print("C/D. PROVENANCE -> RAWDOC")
missing = []
date_mismatch = []
kind_mismatch = []
n_facts = 0
for pid, d in sorted(everyone.items()):
    for f in d["facts"]:
        n_facts += 1
        p = f["provenance"]
        doc = docs.get(p["doc_id"])
        if doc is None:
            missing.append((f["fact_id"], p["doc_id"]))
            continue
        if p.get("published_at") != doc.get("published_at"):
            date_mismatch.append((f["fact_id"], p.get("published_at"), doc.get("published_at")))
        if p.get("source_kind") != doc.get("source_kind"):
            kind_mismatch.append((f["fact_id"], p.get("source_kind"), doc.get("source_kind")))
check("every provenance.doc_id resolves to docs/<doc_id>.json", not missing,
      "%d facts checked against %d docs" % (n_facts, len(docs)))
check("provenance.published_at == its RawDoc published_at", not date_mismatch,
      str(date_mismatch or "all %d facts" % n_facts))
check("provenance.source_kind == its RawDoc source_kind", not kind_mismatch,
      str(kind_mismatch or "all %d facts" % n_facts))

# ---------------------------------------------------------------- E. the tie
print()
print("E. NEWEST-DISPLAYABLE TIE IN runa-okonkwo (a consequence of D, not an accident)")


def displayable(f):
    p = f["provenance"]
    return (not f["excluded"]) and p["confidence"] >= 0.7 and p["source_kind"] in DISPLAYABLE_KINDS


runa = resolved["runa-okonkwo"]
disp = [f for f in runa["facts"] if displayable(f)]
newest = max(f["provenance"]["published_at"] for f in disp)
tied = sorted(f["fact_id"] for f in disp if f["provenance"]["published_at"] == newest)
for fid in tied:
    f = next(x for x in runa["facts"] if x["fact_id"] == fid)
    print("  %-18s %-16s %s  doc=%s" % (fid, f["category"], f["provenance"]["published_at"],
                                        f["provenance"]["doc_id"]))
docs_of_tied = {next(x for x in runa["facts"] if x["fact_id"] == fid)["provenance"]["doc_id"]
                for fid in tied}
check("runa has %d displayable facts" % len(disp), len(disp) >= 3,
      ">= 3, so a 3-slot Lately is fillable under the widest reading")
check("displayable recent_activity facts >= 3", 
      len([f for f in disp if f["category"] == "recent_activity"]) >= 3,
      "%d, so a 3-slot Lately is fillable under the NARROWEST reading too"
      % len([f for f in disp if f["category"] == "recent_activity"]))
check("newest displayable date is a TIE", len(tied) > 1, "%s on %s" % (tied, newest))
check("the tied facts share ONE RawDoc", len(docs_of_tied) == 1,
      "%s - so the tie cannot be broken without contradicting D" % sorted(docs_of_tied))

# ---------------------------------------------------------------- F. smoothed idf
print()
print("F. SMOOTHED IDF OF THE SCORING HUBS - idf = max(0, ln(N / (1 + n)))")
N = len(resolved)
carriers = {}
for pid, d in resolved.items():
    for h in d["hubs"]:
        carriers.setdefault(h["hub_id"], set()).add(pid)
for hid in sorted(carriers):
    n = len(carriers[hid])
    print("  %-36s n=%d  max(0, ln(%d/(1+%d))) = %.6f" % (hid, n, N, n, max(0.0, math.log(N / (1 + n)))))
scoring = ["investor:foundry-seed-2019", "topic:developer-tools-go-to-market"]
check("N == 5", N == 5, str(sorted(resolved)))
for hid in scoring:
    n = len(carriers[hid])
    idf = max(0.0, math.log(N / (1 + n)))
    check("%s: n == 2 and idf == ln(5/3)" % hid, n == 2 and abs(idf - math.log(5 / 3)) < 1e-12,
          "n=%d idf=%.10f  (ln(5/(1+2)), NOT ln(5/2) and NOT 'three carriers')" % (n, idf))

failures = [r for r in results if not r[1]]
print()
print("CHECK 7 RESULT: %d/%d assertions passed" % (len(results) - len(failures), len(results)))
raise SystemExit(1 if failures else 0)
```

### Verbatim output (exit code 0)

```
A. HUB ID CANONICALISATION - DESIGN Hub.hub_id = '{type}:{slug(label)}'
  hub_id                               label                          {type}:{slug(label)}                
  city:austin                          'Austin'                       city:austin                          OK
  company:lantern-freight              'Lantern Freight'              company:lantern-freight              OK
  investor:foundry-seed-2019           'Foundry Seed 2019'            investor:foundry-seed-2019           OK
  school:bellhaven-polytechnic         'Bellhaven Polytechnic'        school:bellhaven-polytechnic         OK
  topic:developer-tools-go-to-market   'Developer-tools go-to-market' topic:developer-tools-go-to-market   OK
  topic:remote-work                    'Remote work'                  topic:remote-work                    OK
  every hub_id is canonical                                      PASS   16 hub entries, 6 distinct hubs, 0 mismatches
  one hub_id carries one (label, type) everywhere                PASS   

B. PERSON ID CANONICALISATION - person_id == slug(person.name)
  jem-arrowood       'Jem Arrowood'           -> jem-arrowood
  mira-hollowell     'Mira Hollowell'         -> mira-hollowell
  runa-okonkwo       'Runa Okonkwo'           -> runa-okonkwo
  sil-vantorre       'Sil Vantorre'           -> sil-vantorre
  theo-baptiste      'Theo Baptiste'          -> theo-baptiste
  vex-tarrow         'Vex Tarrow'             -> vex-tarrow
  every person_id == slug(name)                                  PASS   6 dossiers (5 resolved + 1 unresolved)
  dossier filename == person_id                                  PASS   

C/D. PROVENANCE -> RAWDOC
  every provenance.doc_id resolves to docs/<doc_id>.json         PASS   35 facts checked against 23 docs
  provenance.published_at == its RawDoc published_at             PASS   all 35 facts
  provenance.source_kind == its RawDoc source_kind               PASS   all 35 facts

E. NEWEST-DISPLAYABLE TIE IN runa-okonkwo (a consequence of D, not an accident)
  runa-okonkwo-f05   recent_activity  2026-02-11  doc=92b1d32390d8795f
  runa-okonkwo-f11   affiliation      2026-02-11  doc=92b1d32390d8795f
  runa has 13 displayable facts                                  PASS   >= 3, so a 3-slot Lately is fillable under the widest reading
  displayable recent_activity facts >= 3                         PASS   4, so a 3-slot Lately is fillable under the NARROWEST reading too
  newest displayable date is a TIE                               PASS   ['runa-okonkwo-f05', 'runa-okonkwo-f11'] on 2026-02-11
  the tied facts share ONE RawDoc                                PASS   ['92b1d32390d8795f'] - so the tie cannot be broken without contradicting D

F. SMOOTHED IDF OF THE SCORING HUBS - idf = max(0, ln(N / (1 + n)))
  city:austin                          n=5  max(0, ln(5/(1+5))) = 0.000000
  company:lantern-freight              n=1  max(0, ln(5/(1+1))) = 0.916291
  investor:foundry-seed-2019           n=2  max(0, ln(5/(1+2))) = 0.510826
  school:bellhaven-polytechnic         n=1  max(0, ln(5/(1+1))) = 0.916291
  topic:developer-tools-go-to-market   n=2  max(0, ln(5/(1+2))) = 0.510826
  topic:remote-work                    n=5  max(0, ln(5/(1+5))) = 0.000000
  N == 5                                                         PASS   ['jem-arrowood', 'mira-hollowell', 'runa-okonkwo', 'sil-vantorre', 'theo-baptiste']
  investor:foundry-seed-2019: n == 2 and idf == ln(5/3)          PASS   n=2 idf=0.5108256238  (ln(5/(1+2)), NOT ln(5/2) and NOT 'three carriers')
  topic:developer-tools-go-to-market: n == 2 and idf == ln(5/3)  PASS   n=2 idf=0.5108256238  (ln(5/(1+2)), NOT ln(5/2) and NOT 'three carriers')

CHECK 7 RESULT: 14/14 assertions passed
```

Run against the corpus **before** the hub-id fix, the same script exits 1 on
`every hub_id is canonical ... FAIL   16 hub entries, 6 distinct hubs, 2 mismatches`.
That negative control is reproduced in *Disagreements* section 1: the check is known to
fail on the defect it was written for, not merely to pass on the corpus as fixed.

---

## Reproducing this

The scripts hard-code an absolute path to the repository root, take no arguments, print
their own evidence, and exit non-zero on any failure. **Point that constant at the root of
the tree you want to check** before running them - as committed it names the canonical
repo path, so running them unedited from a worktree or a scratch copy measures the wrong
corpus (check 7 will report `MISMATCH` and exit 1 if the tree it reaches has not had the
hub-id fix). The `python3` used for the recorded outputs has `networkx 2.6.3` and `PyYAML`
available; check 1's networkx cross-check line and check 5 both need them. Run in any
order:

```bash
python3 check1_hub_arithmetic.py
python3 check2_citations.py
python3 check3_doc_ids.py
python3 check4_distinctive_strings.py
python3 check5_fictional.py
python3 check6_contract_and_1b.py
python3 check7_canonical_ids.py
```

All seven exited 0 with their path constant pointed at the tree that contains **this**
file. Checks 1-6 were re-run after the hub-id correction described at the top of this file;
checks 2-6 reproduced their committed output byte for byte, and check 1 differed only in
the hub-id string on three lines, with every number identical.
