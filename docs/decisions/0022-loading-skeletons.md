# 0022 Loading states: keep the frame, draw only what waits

**Status:** accepted · 2026-11-01 · founder instruction

## Context
Screens used to blank out while the app started or a page's code loaded: a bare spinner covered the whole window, the side menu
disappeared, and static things such as titles and tab toggles came back only when the data did. Inside screens, the placeholders were
generic grey boxes that did not match the real layout, and some showed misleading values while loading ("0", "Nothing needs you").

## Decision
1. **The frame never goes away.** Side menu, top bar, bottom bar and theme button are drawn for real from the first paint (`Shell` in
   `booting` mode uses the role stored with the sign-in). Only the names that need data are placeholders.
2. **A page draws its fixed parts at once.** Titles, subtitles and tab toggles are real text; only the parts that wait for data or code
   are grey shapes shaped like the real thing.
3. **Code loading** is covered by a `Suspense` boundary *inside* the shell (around the page area), with one page skeleton per route.
4. **Data loading** inside a screen uses the shared parts (`CardGridSkeleton`, `TableSkeleton`, `ListRowsSkeleton`, `ThreadSkeleton`,
   `ColumnsSkeleton`, `FormSkeleton`, ...). A value that is not known yet is never shown as 0 or "nothing".
5. Everything lives in **`frontend/src/ui/skeletons.tsx`**, next to each other, so a layout change and its skeleton change together.

## Enforcement
`frontend/src/ui/skeletons.test.ts` fails when a route in `App.tsx` has no entry in `PAGE_SKELETONS`, when a skeleton is left for a
route that no longer exists, or when the app goes back to a whole-page spinner.

## How to revisit
Skeletons are plain components; replacing the approach (for example with route-level data loaders) only changes where they are rendered.
If a screen's layout changes, update its entry in `PAGE_SKELETONS` and the shared part it uses in the same commit.
