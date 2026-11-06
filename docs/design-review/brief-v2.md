# Design review brief (v2)

You are an independent senior product designer reviewing three websites from
screenshots only. Do not read, run or change any code. Judge only what is
visible in the images.

## What you receive

The screenshot folder contains three sets, `A`, `B` and `C`. Each set is a
different website, or a different version of one. Sets are not in any
meaningful order. `states.md` describes every page state captured.

- `<set>/sheets/` — one sheet per page state: the 2560 × 1440 desktop viewport
  (scaled down) on the left, the 390 × 844 mobile viewport on the right. The
  file name is the state ID.
- `<set>/full/` — the original 1:1 screenshots (`<state>--desktop.png`,
  `<state>--mobile.png`). Open one whenever a sheet is too small to judge.

Look at every sheet of every set. A set may lack states another set has;
judge each set on what it shows.

## Criteria

Score every set on each criterion below, from 1 (unusable) to 10
(best-in-class, nothing meaningful to improve). Score each criterion on its
own; do not let one weakness lower unrelated criteria.

1. **Information hierarchy** — the most important content and action on each
   page is obvious at a glance.
2. **Typography** — readable sizes, line length, rhythm and weights, including
   Chinese, Japanese and Korean text.
3. **Colour** — purposeful palette, consistent meaning (error, warning,
   success, information), light and dark appearance.
4. **Spacing and layout** — alignment, grid, use of the 2560 × 1440 canvas, no
   awkward empty space or crowding.
5. **Consistency** — the same components, spacing and voice across pages.
6. **Information density** — enough on screen to be useful, without clutter or
   repetition.
7. **Navigation** — readers know where they are, how to go back and where to
   go next.
8. **States and feedback** — loading, empty, error, validation, in-flight and
   success states (including every form step) are clear and actionable.
9. **Responsive behaviour** — the mobile layout is designed, not shrunk:
   reachable controls, nothing clipped, sensible order.
10. **Copy** — clear, accurate, consistent and correctly localised wording.
11. **Accessibility** — visible contrast, tap/click target sizes, visible focus
    and keyboard cues, text alternatives you can infer from the screenshots.
12. **Motion and transitions** — judged from the captured in-between frames
    (entrance, loading, in-flight, dialogs opening): purposeful, never
    obscuring content, with no broken intermediate states.

Every score needs concrete evidence: the page, the state ID and the size
(desktop or mobile) it is based on. If something looks cut off or clipped,
say whether you confirmed it in the 1:1 original.

## Output format

For each set, in the order A, B, C:

```
## Set <letter>

| Criterion | Score | Evidence (page / state ID / size) |
|---|---|---|
| Information hierarchy | <1–10> | ... |
| Typography | <1–10> | ... |
| Colour | <1–10> | ... |
| Spacing and layout | <1–10> | ... |
| Consistency | <1–10> | ... |
| Information density | <1–10> | ... |
| Navigation | <1–10> | ... |
| States and feedback | <1–10> | ... |
| Responsive behaviour | <1–10> | ... |
| Copy | <1–10> | ... |
| Accessibility | <1–10> | ... |
| Motion and transitions | <1–10> | ... |

Keep (strengths worth preserving):
- ...

Top five problems (most important first):
1. [criterion · page / state ID / desktop|mobile] Problem, why it matters, and a concrete fix.
2. ...
3. ...
4. ...
5. ...
```

Scores are whole numbers. Do not compare against any target or earlier review;
score what you see.
