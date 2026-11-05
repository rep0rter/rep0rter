# Design review brief

You are an independent senior product designer reviewing three websites from
screenshots only. Do not read, run or change any code. Judge only what is
visible in the images.

## What you receive

The screenshot folder contains three sets, `A`, `B` and `C`. Each set is a
different news website, or a different version of one. Sets are not in any
meaningful order. `states.md` describes every page state captured.

- `<set>/sheets/` — one sheet per page state: the 2560 × 1440 desktop viewport
  (scaled down) on the left, the 390 × 844 mobile viewport on the right. The
  file name is the state ID.
- `<set>/full/` — the original 1:1 screenshots (`<state>--desktop.png`,
  `<state>--mobile.png`). Open one whenever a sheet is too small to judge.

Look at every sheet of every set. A set may lack states another set has;
judge each set on what it shows.

## Scoring criteria

Score each set from 1 (unusable) to 10 (best-in-class, nothing meaningful to
improve), weighing:

1. Visual hierarchy: is the most important content obvious at a glance?
2. Typography: readable sizes, line length, rhythm, multilingual (CJK, Hangul)
   rendering.
3. Layout and spacing: use of the 1440p canvas, alignment, density, no awkward
   empty space or crowding.
4. Colour and contrast: purposeful, accessible, consistent in light and dark.
5. Consistency: the same components, spacing and voice across all pages.
6. State design: loading, empty, error, validation, success and in-flight
   states are clear, reassuring and actionable.
7. Mobile ergonomics: tap targets, reachable controls, no cramped or clipped
   content at 390 px.
8. Navigation and wayfinding: users know where they are and how to go on.
9. Forms: labels, help, grouping, feedback, primary action clarity.
10. Character and polish: a distinct, credible identity; details feel finished.

## Output format

For each set, in the order A, B, C:

```
## Set <letter> — <score>/10

Keep (strengths worth preserving):
- ...

Top five problems (most important first):
1. [page / state ID / desktop|mobile] Problem, why it matters, and a concrete fix.
2. ...
3. ...
4. ...
5. ...
```

Every problem must name the page, the state ID and the size. If something
looks cut off or clipped, say whether you confirmed it in the 1:1 original.
Scores are whole numbers. Do not compare against any target or earlier review;
score what you see.
