# Design review scores

Raw per-round scores from the UI hill climb. The blind A/B/C letters were mapped back to sets after each round; mappings were kept outside the reviewer folders. Briefs (`brief.md`, `brief-v2.md`) were never edited; scores are not compared across brief versions.

## Brief v1 (rounds 1–9, single reviewer, overall score 1–10)

| Round | Reviewer | Baseline | New | Reference | New − Baseline | Notes |
|---|---|---|---|---|---|---|
| 1 | Opus 5.5 | 6 (B) | 6 (C) | 6 (A) | 0 | New = unchanged main (noise calibration); reviewer found B≡C. Reference had RoW bot walls → recaptured after round 1. |
| 2 | Opus 5.5 | 5 (B) | 7 (C) | 7 (A) | +2 | Baseline 6→5 (drift −1). Reference first scored on corrected set. |
| 3 | Opus 5.5 | 5 (A) | 7 (B) | 6 (C) | +2 | Baseline steady at 5 (8 states recaptured with fixed scroll). Reference 7→6. |
| 4 | Opus 5.5 | 5 (B) | 8 (A) | 7 (C) | +3 | Baseline steady 5. Reference 6→7. |
| 5 | Opus 5.5 | 5 (C) | 7 (B) | 7 (A) | +2 | New 8→7 (no regression found; reviewer variance). Baseline steady 5. |
| 6 | Opus 5.5 | 6 (B) | 8 (C) | 7 (A) | +2 | Baseline 5→6 (drift +1). |
| 7 | Opus 5.5 | 5 (B) | 8 (A) | 6 (C) | +3 | Baseline 6→5. Reference 7→6. |
| 8 | Opus 5.5 | 5 (C) | 8 (A) | 7 (B) | +3 | |
| 9 | Opus 5.5 | 4 (C) | 7 (A) | 7 (B) | +3 | Baseline 5→4 (drift −1). Last v1 round; switch refactor follows. |

## Brief v2 (rounds 1–8, council, 12 criteria 1–10)

Criteria order: Hierarchy, Typography, Colour, Spacing, Consistency, Density, Navigation, States, Responsive, Copy, A11y, Motion

| v2 round | Reviewer | Set | Hier | Typo | Col | Space | Cons | Dens | Nav | States | Resp | Copy | A11y | Motion | Min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Opus 5.5 | New (C) | 8 | 7 | 8 | 7 | 8 | 7 | 8 | 8 | 7 | 8 | 7 | 7 | 7 |
| 1 | Opus 5.5 | Baseline (A) | 6 | 5 | 6 | 5 | 6 | 5 | 6 | 3 | 6 | 6 | 5 | 4 | 3 |
| 1 | Opus 5.5 | Reference (B) | 7 | 7 | 7 | 6 | 5 | 7 | 7 | 4 | 7 | 7 | 5 | 5 | 4 |
| 1 | GPT-6.1 Sol | New (C) | 8 | 8 | 8 | 7 | 8 | 6 | 8 | 8 | 7 | 8 | 8 | 7 | 6 |
| 1 | GPT-6.1 Sol | Baseline (A) | 7 | 7 | 7 | 6 | 8 | 6 | 7 | 6 | 7 | 6 | 7 | 5 | 5 |
| 1 | GPT-6.1 Sol | Reference (B) | 7 | 7 | 8 | 6 | 7 | 6 | 7 | 5 | 6 | 7 | 6 | 6 | 5 |

v2 round 1 divergences:
- Baseline: Sol is far more lenient than Opus (States 6 vs 3, Typography 7 vs 5, Consistency 8 vs 6, Motion 5 vs 4).
- New: Typography (Opus 7 / Sol 8), Density (Opus 7 / Sol 6), Accessibility (Opus 7 / Sol 8).
- Entrance: Opus finds the dimmed page acceptable (7); Sol says the strong fade impairs reading.
- Language status: Opus now flags the in-flow bar's 55px layout shift (v1 round 5 asked for push-down).
- Shared: "Show 0 stories" on invalid range; sticky Show-N bar overlaps the sort control; "November 21"/"Community stories" (fixture data, also in baseline).
| 2 | GPT-6.1 Sol | New (C) | 8 | 8 | 8 | 8 | 8 | 7 | 8 | 8 | 7 | 7 | 8 | 7 | 7 |
| 2 | GPT-6.1 Sol | Baseline (A) | 6 | 6 | 7 | 5 | 8 | 5 | 7 | 6 | 6 | 6 | 6 | 4 | 4 |
| 2 | GPT-6.1 Sol | Reference (B) | 7 | 6 | 8 | 6 | 6 | 6 | 7 | 4 | 6 | 6 | 6 | 5 | 4 |
| 2 | Opus 5.5 | New (C) | 8 | 8 | 8 | 7 | 8 | 7 | 8 | 8 | 8 | 8 | 7 | 7 | 7 |
| 2 | Opus 5.5 | Baseline (A) | 6 | 6 | 5 | 5 | 6 | 6 | 7 | 4 | 6 | 6 | 5 | 4 | 4 |
| 2 | Opus 5.5 | Reference (B) | 7 | 6 | 7 | 5 | 5 | 7 | 7 | 4 | 7 | 7 | 5 | 5 | 4 |

v2 round 2 divergences:
- Language status: round 1 Opus penalised the in-flow bar's layout shift; round 2 both Opus and Sol penalise the overlay for covering the hero → both now agree on in-flow push-down (adopted).
- Entrance: both now accept it (7/7); round 1 Sol had flagged the fade.
- Spacing: Sol 8 vs Opus 7 (Opus pixel-checked a remaining backdrop strip).
- Responsive: Opus 8 vs Sol 7.
- Owner feedback (priority): visual noise — declutter pass next round.
| 3 | GPT-6.1 Sol | New | 8 | 7 | 8 | 8 | 8 | 7 | 8 | 8 | 8 | 7 | 7 | 6 | 6 |
| 3 | GPT-6.1 Sol | Baseline | 6 | 7 | 8 | 6 | 7 | 5 | 7 | 5 | 6 | 6 | 6 | 4 | 4 |
| 3 | GPT-6.1 Sol | Reference | 7 | 7 | 8 | 6 | 7 | 6 | 7 | 5 | 6 | 7 | 6 | 5 | 5 |
| 3 | Opus 5.5 | New | 8 | 7 | 8 | 6 | 8 | 7 | 7 | 8 | 8 | 7 | 7 | 6 | 6 |
| 3 | Opus 5.5 | Baseline | 6 | 6 | 6 | 5 | 6 | 5 | 7 | 4 | 6 | 6 | 5 | 4 | 4 |
| 3 | Opus 5.5 | Reference | 7 | 7 | 7 | 6 | 4 | 7 | 7 | 4 | 7 | 7 | 5 | 5 | 4 |
(Opus 5.5 round 3: first attempt failed with "Claude API rate limit reached. Try again later." — no retry-after/reset time given; rerun after the 70-minute cooldown with a new delegate.)
| 3 | Fable 5.1 | New | 8 | 7 | 8 | 5 | 7 | 7 | 7 | 8 | 7 | 8 | 6 | 6 | 5 |
| 3 | Fable 5.1 | Baseline | 5 | 6 | 6 | 5 | 6 | 5 | 6 | 6 | 6 | 6 | 5 | 4 | 4 |
| 3 | Fable 5.1 | Reference | 7 | 7 | 6 | 5 | 5 | 6 | 6 | 3 | 6 | 7 | 5 | 5 | 3 |
(Fable 5.1 round 3: first attempt failed with the same 429 message, no reset time; rerun with a new delegate.)

v2 round 3 consensus and divergences:
- All three: entrance wordmark overlaps search placeholder (Motion 6/6/6); ~11px desktop meta text; report card repeats headline/summary (conflicts with owner: original must stay readable — keep, size card to content).
- Opus + Fable: 2560 canvas still ~60% empty, hashtag rail ends early (Spacing 6/5 vs Sol 8).
- Fable only: account header drops search/Write; untranslated-ko report card fixed height with blank block; globe/appearance icons unlabelled.
- Sol only: write preview lacks adjacent edit/publish actions; presentation demo header overlap on mobile.
- Opus only: no jump-to-date navigation; pending headline shown as headline.
| 4 | GPT-6.1 Sol | New | 8 | 8 | 9 | 8 | 9 | 7 | 8 | 9 | 8 | 8 | 8 | 8 | 7 |
| 4 | GPT-6.1 Sol | Baseline | 5 | 7 | 7 | 6 | 8 | 5 | 6 | 5 | 7 | 6 | 6 | 4 | 4 |
| 4 | GPT-6.1 Sol | Reference | 7 | 7 | 8 | 6 | 7 | 6 | 7 | 5 | 6 | 8 | 6 | 6 | 5 |
| 4 | Opus 5.5 | New | 8 | 6 | 8 | 7 | 8 | 8 | 8 | 8 | 8 | 8 | 7 | 7 | 6 |
| 4 | Opus 5.5 | Baseline | 6 | 5 | 6 | 4 | 5 | 5 | 6 | 4 | 6 | 6 | 5 | 4 | 4 |
| 4 | Opus 5.5 | Reference | 7 | 6 | 7 | 5 | 5 | 6 | 7 | 4 | 6 | 7 | 5 | 5 | 4 |

v2 round 4 divergences:
- Typography: Opus 6 (meta/chips/helper still ~11–12px effective at 2560) vs Sol 8.
- Density: Sol 7 (report card repeats copy — owner keeps it readable) vs Opus 8.
- Both: feed-card thumbnails in multi-column cards small/ragged (Opus) ; singleton card line too long (Sol); entrance dimmed feed reads as disabled (Opus); globe/appearance unlabelled (Sol).
- Opus only: image-viewer-error Try again left-aligned; helper text contrast.
- Sol only: write-preview actions below fold.
| 5 | GPT-6.1 Sol | New | 8 | 8 | 8 | 7 | 8 | 7 | 8 | 9 | 8 | 7 | 8 | 8 | 7 |
| 5 | GPT-6.1 Sol | Baseline | 6 | 7 | 7 | 6 | 8 | 5 | 7 | 5 | 7 | 6 | 6 | 4 | 4 |
| 5 | GPT-6.1 Sol | Reference | 7 | 7 | 8 | 6 | 6 | 6 | 7 | 5 | 6 | 7 | 6 | 6 | 5 |
| 5 | Opus 5.5 | New | 8 | 8 | 8 | 7 | 8 | 8 | 8 | 9 | 8 | 8 | 7 | 7 | 7 |
| 5 | Opus 5.5 | Baseline | 6 | 6 | 5 | 5 | 6 | 6 | 6 | 4 | 6 | 6 | 5 | 3 | 3 |
| 5 | Opus 5.5 | Reference | 7 | 6 | 7 | 5 | 5 | 7 | 7 | 3 | 7 | 7 | 4 | 5 | 3 |

v2 round 5 consensus and divergences:
- Both: latest revision not marked near the headline; report card repeats text (owner keeps it readable).
- Entrance: Sol penalises hidden header controls during the brand moment; earlier rounds penalised overlap → keep controls visible and animate only the logo inside its own slot.
- Language status: Opus notes the push-down shift (26px); v2r2 both preferred push-down over overlay → keep.
- Opus only: three-up cards stretch with empty halves; translucent image dialog; empty home lacks an action; banner + inline error repeat the same sentence.
- Sol only: lone filtered card too large; presentation demo headline below frame.
- Fixture bug: zh/ja/ko seeded translations contain English "November 21"; fixed in the preview seed (UI-independent).
| 6 | GPT-6.1 Sol | New | 8 | 8 | 9 | 7 | 8 | 6 | 8 | 8 | 8 | 8 | 8 | 8 | 6 |
| 6 | GPT-6.1 Sol | Baseline | 7 | 7 | 7 | 6 | 8 | 5 | 7 | 6 | 6 | 6 | 7 | 4 | 4 |
| 6 | GPT-6.1 Sol | Reference | 7 | 7 | 8 | 6 | 7 | 6 | 7 | 5 | 7 | 7 | 6 | 6 | 5 |
(v2r06 capture note: after the last-row span fix only home-en, home-en-feed, home-search-results and source-timeline were recaptured; home-en-dark and others still showed the old lone narrow card, which Sol flagged. Next rounds recapture everything after any CSS change.)
| 6 | Opus 5.5 | — | failed: "Claude API rate limit reached. Try again later." (no retry-after/reset given) ~06:40 TW |
| 6 | Fable 5.1 | — | failed: same 429 message, no retry-after/reset ~06:40 TW |
(v2r06 resolution: Sol's v2r06 review is kept and labelled "106 of 114 new-set captures predate the last CSS fix". The full new set was recaptured at 3826fd6 into v2r06b with the same A/B/C letters; Opus and Fable are rerun on v2r06b with new delegates.)
| 6 | Fable 5.1 | — | rerun on v2r06b failed again: "Claude API rate limit reached. Try again later." (no retry-after/reset) at 07:22 TW; not retried immediately |
| 6 | Opus 5.5 (v2r06b) | New | 8 | 7 | 8 | 6 | 7 | 7 | 8 | 8 | 8 | 7 | 7 | 6 | 6 |
| 6 | Opus 5.5 (v2r06b) | Baseline | 5 | 6 | 4 | 4 | 5 | 5 | 6 | 3 | 6 | 5 | 4 | 3 | 3 |
| 6 | Opus 5.5 (v2r06b) | Reference | 7 | 7 | 7 | 6 | 5 | 8 | 7 | 4 | 8 | 7 | 5 | 4 | 4 |

v2 round 6 consensus and divergences:
- Both: feed rows with fewer cards than columns leave empty space (Opus: right third of home-en; Sol: home-en-dark on the stale set).
- Opus: image viewer not larger than inline (desktop), unreadable on phones; back labels differ ("Back to all stories" / "Back to the feed"); entrance now indistinguishable from the settled page.
- Language status: Opus r6 prefers an overlay or header progress line over the 27px push; v2r2 both preferred push-down over a covering overlay → kept push-down; recorded as an unresolved reviewer conflict.
- Sol: superseded story's report card shows obsolete info without its own label; tag timeline intro tall on phones.
- Pending English headline: both again flag it; editorial policy forbids untranslated text in the English edition → kept.
| 6 | Fable 5.1 | — | second rerun on v2r06b failed: "Claude API rate limit reached. Try again later." (no retry-after/reset) at 07:48 TW; three 429s this round — round 6 closes without Fable |
| 7 | GPT-6.1 Sol | New | 8 | 8 | 8 | 7 | 9 | 6 | 8 | 8 | 8 | 8 | 8 | 8 | 6 |
| 7 | GPT-6.1 Sol | Baseline | 6 | 8 | 7 | 6 | 8 | 5 | 7 | 5 | 7 | 6 | 7 | 4 | 4 |
| 7 | GPT-6.1 Sol | Reference | 7 | 7 | 8 | 6 | 7 | 6 | 7 | 5 | 6 | 7 | 6 | 6 | 5 |
| 7 | Opus 5.5 | New | 8 | 7 | 8 | 7 | 8 | 7 | 8 | 8 | 8 | 8 | 7 | 6 | 6 |
| 7 | Opus 5.5 | Baseline | 6 | 5 | 6 | 4 | 6 | 6 | 7 | 5 | 6 | 6 | 5 | 4 | 4 |
| 7 | Opus 5.5 | Reference | 7 | 7 | 7 | 6 | 5 | 6 | 7 | 3 | 6 | 7 | 5 | 5 | 3 |

v2 round 7 consensus and divergences:
- Both: report card repeats the headline and summary (Density 7/6) — owner keeps the original readable, never thumbnail/collapsed → kept.
- Opus: hashtag row peeks under the sticky header when scrolled (Motion 6); desktop language/theme buttons ~28px; story column leaves the right third empty; home lacks a one-line value statement (conflicts with owner's declutter → kept removed).
- Sol: superseded report card needs a superseded mark on the image itself; sparse search groups leave half rows; phone invalid-range error separated from its date fields.
- Pending English headline: both flag again; editorial policy → kept.
| 8 | GPT-6.1 Sol | New | 9 | 8 | 9 | 8 | 9 | 7 | 8 | 9 | 8 | 8 | 8 | 8 | 7 |
| 8 | GPT-6.1 Sol | Baseline | 7 | 8 | 7 | 5 | 8 | 5 | 7 | 5 | 6 | 6 | 7 | 3 | 3 |
| 8 | GPT-6.1 Sol | Reference | 8 | 7 | 8 | 6 | 7 | 6 | 7 | 5 | 6 | 7 | 6 | 5 | 5 |
| 8 | Opus 5.5 | New | 8 | 7 | 8 | 7 | 8 | 7 | 8 | 8 | 8 | 8 | 7 | 7 | 7 |
| 8 | Opus 5.5 | Baseline | 6 | 5 | 5 | 4 | 4 | 5 | 6 | 3 | 5 | 5 | 4 | 4 | 3 |
| 8 | Opus 5.5 | Reference | 7 | 7 | 8 | 6 | 5 | 7 | 7 | 4 | 7 | 7 | 5 | 4 | 4 |

v2 round 8 (cap) consensus and divergences:
- Both: phone image viewer leaves large empty bands around a landscape image; desktop meta/chip text still small.
- Density: Sol 7 (report card repeats the headline — owner keeps the original readable) vs Opus 7 ("no duplicate content") — same score, different reasons.
- Motion: Sol 8 (entrance leaves content unobscured) vs Opus 7 (entrance frame indistinguishable; language status push-down still preferred as overlay — unresolved conflict with v2r2 consensus).
- Spacing: Sol 8 vs Opus 7 (forms/story/sign-in use half the 2560 canvas).
- Sol only: #air-quality and #airquality shown as separate topics (fixture data, not UI); localised editions keep English participation instructions under an "original, untranslated" label (editorial policy).
- Opus only: projects-empty lacks a "no projects yet" line; write-success category vs source label mismatch (fixture); back link under header on story-revision-latest scroll position.
- Pass rule (every criterion ≥9 from every reviewer) not met at the 8-round cap. Stopped per rule.

## Residual fixes check (2026-11-12, brief v2, one blind reviewer)

Before/after sheets at 2560, 1440 and 390: `residual-fixes/`. Blind letters: new = B, baseline (main) = C, reference = A.

| Reviewer | Set | Hier | Typo | Col | Space | Cons | Dens | Nav | States | Resp | Copy | A11y | Motion | Min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Opus 5.5 (high) | New (B) | 8 | 7 | 8 | 7 | 8 | 7 | 8 | 9 | 8 | 8 | 7 | 6 | 6 |
| Opus 5.5 (high) | Baseline (C) | 8 | 7 | 8 | 6 | 7 | 6 | 7 | 8 | 7 | 7 | 7 | 6 | 6 |
| Opus 5.5 (high) | Reference (A) | 7 | 7 | 7 | 5 | 4 | 6 | 7 | 4 | 7 | 7 | 5 | 4 | 4 |

- No criterion regressed; Spacing, Consistency, Density, Navigation, States, Responsive and Copy rose by one.
- The reviewer's baseline problems were the fixed items: half-canvas forms/story, projects-empty without guidance, phone viewer bands, "Community stories"/"Community submissions".
- Remaining on new: report card repeats the headline (owner keeps it), entrance frame indistinguishable, sign-in/withdrawn cards in a large canvas, small header labels.
