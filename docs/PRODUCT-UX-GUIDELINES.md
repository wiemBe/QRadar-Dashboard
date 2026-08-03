# Product UX Guidelines

Normative UX and semantic contracts for the QRadar behavioral anomaly and
investigation platform's web interface.

This document describes what is implemented today. It is the reference for
anyone changing the frontend, exposing UI-facing API data, writing tests, or
reviewing a UI change — including automated agents working in this repository.
Where it uses **must** / **must not**, the rule is an established product
invariant with regression coverage behind it; **should** is strong design
guidance; **may** marks a permitted alternative.

Companion documents:
[Behavioral Analytics Architecture](BEHAVIORAL-ANALYTICS-ARCHITECTURE.md) for
the engine, lifecycle and explanation model;
[Phase A design](PHASE-A-SOURCE-VOLUME-ANOMALY.md) for the source-volume
detector.

---

## 1. What this product is

This is **not** a generic QRadar dashboard, and it must not drift into one. A
dashboard reports the state of a system. This product detects that a log
source's behavior *changed*, and then explains *what* changed.

Its purpose is to:

- detect behavioral changes in log sources against a learned seasonal baseline;
- compare observed volume against expected volume;
- surface volume spikes, volume drops, and `NO_EVENTS` silence;
- represent incident lifecycle accurately;
- provide Ariel contributor evidence answering **"what changed?"**;
- support investigation without overstating certainty or claiming causation.

Everything else the interface carries — offenses, rules, coverage, inventory —
is a supporting capability. Supporting routes must not be given the visual
weight, the hierarchy, or the development attention of the behavioral ones.

A feature that does not help an analyst answer *"did this source change, and
what changed?"* does not belong on the behavioral routes.

---

## 2. Product UX principles

Each principle below is tied to observable behavior, because a principle that
cannot be violated in code cannot be reviewed.

**Investigation before decoration.** The first screen of any behavioral route
must answer the question the route exists for. A page that opens with equally
weighted counters answers nothing: the overview carries four counters, not ten,
and the source page opens with its identity and four metrics rather than a
technical collection table.

**Semantic correctness before visual convenience.** When a layout is easier to
build by blurring two states that mean different things, the layout loses. A
missing measurement must not be drawn as a zero because a gap is awkward to
render.

**Progressive disclosure.** Secondary and technical material is present,
reachable, and collapsed. Nothing is deleted to simplify a page; it is deferred.
If an analyst needs the raw AQL, it is one interaction away, and it is never in
the way of the answer.

**Evidence honesty.** The interface states what it knows, how it knows it, and
what it did not check. Absence of evidence is rendered as absence, never as a
clean result. This is the single most important rule in this document: four of
the six evidence states produce a page with no contributors, which looks
identical to "we looked and nothing stood out."

**Deterministic language.** The same input produces the same sentence. Summary
text is generated from observed values and known state, not composed freely.

**No unsupported causation claims.** The engine reports what changed, never why.
See §7.

**Clear separation of overview, triage, investigation, and technical detail.**
Each route has one job (§3). Work that belongs to another route must not be
duplicated into it because it was convenient.

**Reusable components before route-specific duplication.** A presentation
problem is fixed once, in the shared component or the shared stylesheet, not
patched per call site. A rule keyed to one page's markup will be wrong on the
next page that renders the same thing.

**Responsive containment rather than clipping.** Layout defects are fixed
structurally. Hiding overflow is not a fix; it is the defect plus concealment
(§9).

---

## 3. Information architecture

### `/behavior` — behavioral overview

Answers *"what needs me now?"*.

- Carries a small, fixed set of decision-relevant KPI cards.
- Carries a worklist of sources requiring attention, recently closed incidents,
  and the fleet's health distribution.
- Links onward into source investigation.

This route **must not** carry the complete source inventory. A landing page
listing every monitored source buries the handful of rows that require action
beneath the majority that do not, and inverts the page's purpose.

The count of sources with no adequate baseline is decision-relevant and keeps a
primary card. It **must not** be presented as healthy: a source that is not
being judged is an observability gap, and a fleet reporting "0 anomalies" over
unbaselined sources is reporting the absence of detection as the absence of
problems.

### `/behavior/sources` — source inventory

Answers *"which source do I want?"*, for an analyst who is looking deliberately.

- Searchable, sortable, filterable, server-paginated inventory.
- Health- and lifecycle-oriented discovery.
- Links to source detail.

This route **may** scroll its table locally. It **must not** produce page-level
horizontal overflow (§9).

Collection internals belong on the source detail page, not in the inventory row.

### `/behavior/sources/[id]` — source behavior

Answers six questions in a fixed order. The order is the deliverable and is
covered by page-composition tests:

1. **Compact source identity/header** — who this source is, and the lifecycle or
   collection status needed to read the rest. It must not restate the metrics
   below it.
2. **Exactly four primary metrics** — Observed EPS, Expected EPS, Deviation,
   Last event. No fifth primary KPI card may be added. Baseline internals,
   sample counts and collection statistics are not primary metrics.
3. **The observed-versus-expected timeline** — the dominant visual element of
   the page.
4. **Active and recent anomalies** — directly beneath the chart.
5. **Baseline quality** — is the expectation trustworthy?
6. **Collection health** — did we actually observe this window?
7. **Technical details** — raw buckets, baseline history, metadata.

Sections 5–7 are secondary and use progressive disclosure. Collapsing them
**must not** hide a critical `UNAVAILABLE` or `FAILED` condition: the disclosure
summary carries the state even when the body is closed.

### `/anomalies` — triage

Answers *"which incident do I open first?"*.

- Rapid comparison across incidents: state, severity, detector, source, timing.
- Filters, chips and pagination.
- Links into the investigation view.

This route **must not** become a dense technical evidence dump. Version stamps,
provenance and confidence internals belong behind a disclosure or on the
investigation page.

### `/anomalies/[id]` — investigation

The flagship view. Answers *"what changed during the anomalous interval?"*.

Established hierarchy:

- **Deterministic incident summary**, prominent and first.
- **Exactly four primary metrics.** No fifth primary card.
- **Timeline**, before the raw technical detail.
- **Evidence investigation** — the contributor headline and the dimension
  explorer.
- **A dimension-diverse contributor headline** (§6).
- **One contributor table on arrival.** The page must not restore the
  multi-table initial render it replaced.
- **Technical and AQL material collapsed by default**, behind the established
  tab and disclosure presentation.

---

## 4. Measurement and bucket semantics

Three states that a naive interface collapses into "0". They mean different
things and **must** be rendered differently.

### COMPLETE zero

- The bucket was collected successfully and in full.
- The measured value is genuinely zero.
- **May** be rendered as `0` or `0 EPS`.
- Represents **observed silence** — a real finding, and the basis of a
  `NO_EVENTS` anomaly.
- **Must not** be conflated with absent data.

### MISSING

- No reliable observation exists for the interval.
- **Must not** be rendered as `0`.
- **Must not** be interpreted or styled as healthy or normal.
- Charts **must** show a gap or an explicit missing state.

### PARTIAL

- The observation is incomplete.
- **Must not** support a claim of exact zero, exact volume, exact drop, or
  exact normality.
- **Must** be presented explicitly as partial or incomplete.
- **May** be excluded from definitive chart values and summarized separately.

### Rules that follow

- **Null is not measured zero.** A value that was never measured renders as an
  em dash; a value measured as zero renders as `0`. This applies to every figure
  on every behavioral route, including contributor counts, deviation ratios and
  durations.
- **The chart must not invent continuity.** `PARTIAL` and `MISSING` buckets are
  plotted as null, and the line must not connect across them. Drawing a line
  across an uncollected interval turns a collector outage into an apparent
  source outage.
- **Critical information must not exist only in chart geometry or color.** The
  chart is a canvas and is opaque to assistive technology. Anything the chart
  alone would carry must also be available as text (§10).
- **Collection health counts intervals, not rows.** An interval with no stored
  bucket is uncollected. Counting only stored buckets marked incomplete
  produces statements that are individually true and jointly misleading — "115
  of 115 fully observed" beside a chart drawn mostly as gaps.

---

## 5. Lifecycle semantics

The active anomaly states are exactly:

- `CANDIDATE`
- `OPEN`
- `RECOVERING`

Normative rules:

- Active state **must** be derived from the explicit lifecycle state.
- `resolved_at IS NULL` **must not** be used as the definition of active. It is
  lifecycle metadata, not the source of truth for current state.
- `RESOLVED` is **not** active.
- A `NORMAL` row with no resolved timestamp is **not** an active incident.
- An `OPEN`, `CANDIDATE` or `RECOVERING` row **must** remain active even if the
  payload carries a non-null `resolved_at`. Inconsistent data is interpreted
  through the explicit state, in both directions.
- `INSUFFICIENT_DATA` **must not** be presented as healthy, normal, successful,
  or green. It means no detector has judged the source.
- `SUPPRESSED` is neither a finding nor an all-clear and should stay neutral.
- UI copy **must not** invent recovery, resolution, or health from missing
  metadata. A still-running incident has no end time; it must not be given one,
  and its duration is unmeasured rather than `0s`.

---

## 6. Evidence semantics and contributor presentation

The six evidence states and their engine-side meanings are defined in
[the architecture document](BEHAVIORAL-ANALYTICS-ARCHITECTURE.md). What follows
is what the interface is permitted to claim for each.

### COMPLETE / AVAILABLE

- Contributor data **may** be presented normally.
- Counts and comparisons **must** still match the actual API contract. Complete
  evidence does not license a figure the backend did not return.

### TRUNCATED

- The returned contributor rows **may** be shown: they are a real observation.
- The UI **must** indicate clearly that the result is truncated or
  prefix-limited.
- Exact total cardinality **must not** be claimed.
- A complete set of **new** contributors **must not** be claimed.
- A complete set of **disappeared** contributors **must not** be claimed. These
  counts render as indeterminate rather than as the backend's storage default
  of `0`.
- Truncated evidence **must not** silently look complete. The truncation warning
  is stated once, globally, naming the capped dimensions.

### UNAVAILABLE

- **Must** be explicit, and rendered as its own section rather than omitted. An
  absent dimension reads to an analyst as one that *was* examined and came back
  clean.
- **Must not** look like an empty successful result.
- **Must not** be converted to zero.
- **Must not** be presented as healthy.
- It is a property of the source's DSM output, not an error.

### FAILED

- **Must** be explicit.
- **Must** be distinguishable from `UNAVAILABLE` — one is a characteristic of
  the data, the other a transient collection error.
- **Must** be distinguishable from a legitimate empty `COMPLETE` result.
- Wording **must** be understandable without inspecting a raw payload. The
  backend's sanitized reason is rendered as text, never as markup.

### Contributor presentation

- The headline is limited to **three** contributors.
- **Dimension diversity must be preserved.** When eligible contributors exist in
  more than one dimension, the headline must not consist of the three largest
  rows from a single dimension. Selection is ordered by dimension priority and
  takes at most one contributor per dimension, so a noisy source IP list cannot
  crowd out the event name that actually explains the change.
- What `TRUNCATED` evidence permits is bounded by the rules above, in the
  headline as much as in the table.
- Contributor labels **should** remain meaningful when values are long: prefer
  the resolved label over the raw identifier, fall back to the raw value when
  there is none, and wrap rather than widen the row.
- Technical payloads and query provenance belong behind progressive disclosure.

---

## 7. Summary and content rules

Incident summaries are generated, not written. They **must** be:

- deterministic — the same fixture produces the same sentence;
- descriptive rather than causal;
- derived from observed values and known lifecycle and evidence state;
- explicit about uncertainty and about evidence that is unavailable.

### Prohibited language

Summary and status copy **must not** use, or imply:

- "caused by"
- "root cause"
- "attacker"
- "compromise"
- "malicious"
- "because of"

unless the conclusion is supported by a separate trusted product signal that has
been deliberately added to the product contract. No such signal exists today.
Anthropomorphic or speculative explanation is prohibited on the same grounds.

### Preferred forms

| Instead of | Write |
|---|---|
| "traffic dropped because the firewall failed" | "Volume was below the expected range" |
| "the source stopped sending" | "No events were observed in the complete bucket" |
| an empty contributor panel | "Contributor evidence is unavailable" |
| a capped list shown as the whole list | "The returned evidence is truncated" |

Permitted hedged forms, consistent with the architecture document: "largest
observed contributor", "largest volume change", "behavior is consistent with",
"evidence suggests".

---

## 8. Component contracts

Each reusable component owns an invariant. Fix presentation problems in the
component or the shared stylesheet; do not re-solve them per route.

### `AppNav`

Grouped navigation. The active destination carries `aria-current="page"` and
exactly one link carries it. Inactive destinations carry no `aria-current`
attribute at all. A detail route marks its owning section, never that section's
path prefix. Link names must be accessible and unambiguous: no two destinations
may share an accessible name. The shell holds no heading element.

### `PageHeader`

Owns the document's single `h1` and names the page. Pages compose sections
beneath it starting at `h2`. Child content **must not** introduce a second
page-level `h1`.

### `StatCard`

A primary, decision-relevant metric. **Must not** be used to turn every
available field into a KPI — the limits in §3 are the contract. Its content must
shrink or wrap rather than widen the page, and its optional note is where a
counter carries its own caveat, next to the number rather than in a paragraph
further down.

### `Tabs`

Real ARIA tab semantics: a labelled tab list, exactly one tab with
`aria-selected="true"` and the rest explicitly `false`, and valid
tab-to-panel relationships in both directions. Only the active panel is
rendered, so a deferred panel's content stays out of the document. Repeated
instances **must not** produce duplicate IDs; identifiers are generated per
instance.

### `Disclosure`

Native `<details>` / `<summary>`. Keyboard operable, screen-reader announced,
and functional before hydration.

**Do not** replace working native semantics with redundant ARIA state. Browsers
already expose `<summary>` as a button with an expanded state derived from the
`open` attribute; a hand-maintained `role="button"` and `aria-expanded` would
duplicate that and can drift out of sync. If a test environment cannot see the
native mapping, assert the element's `open` state and real visibility instead of
changing the component.

Secondary technical content **should** begin collapsed.

### `CodePanel`

AQL and raw technical content, with a labelled copy control. Collapsed unless
the technical content is part of the user's immediate decision. The code is
rendered as text and never as markup.

### `TableScroll`

A table's local scroll container.

- Local table scrolling is allowed. Page-level horizontal scrolling is not.
- `.table-scroll` **must** remain a positioned element. It is the containing
  block for screen-reader-only and other absolutely positioned table-local
  content; without it, an `.sr-only` element inside a table wider than its
  container resolves against the initial containing block, is laid out past the
  right edge of the viewport, and widens the document even though the table
  itself scrolls correctly.
- **Do not** remove that positioning contract without equivalent regression
  protection.
- The region is labelled and keyboard-reachable, so a table that scrolls can be
  scrolled without a pointer.

### `EvidenceBanner`

Communicates evidence completeness and its limitations, once and globally.
Meaning **must not** rely on color alone: the status is named in text, and the
reason a count is withheld is explained rather than shown as zero.

### `DimensionExplorer`

Displays one active contributor dimension and one table at a time; switching
dimensions must not add a second table. Every dimension is listed, including
those never collected, and each carries its status. Evidence-state limitations
travel with the dimension: an `UNAVAILABLE` dimension shows a status panel
rather than an empty table, and a `TRUNCATED` one keeps its rows while
withholding its counts.

### `LifecycleTimeline`

Exposes lifecycle events as meaningful text in an ordered list, so the sequence
is conveyed structurally. Timestamps use real `time` elements. Timeline position
and color **must not** be the only carriers of meaning, and decorative markers
are hidden from assistive technology. An unmeasured transition value renders as
an em dash, never as zero.

### `TechnicalDetails`

Secondary and raw implementation detail — thresholds, versions, provenance.
**Must not** dominate the initial investigation hierarchy, and starts collapsed.

---

## 9. Responsive layout

### Supported verification viewports

- 1440 × 900
- 1280 × 800
- 1024 × 768

### Acceptance condition

At every supported viewport, on every verified route:

```javascript
document.documentElement.scrollWidth ===
document.documentElement.clientWidth
```

### Rules

- There **must** be no page-level horizontal overflow.
- Local table scrolling **is** acceptable, and is the intended mechanism for a
  table wider than its column. A scroll container's own
  `scrollWidth > clientWidth` is not a page-level defect and must not be
  reported as one.
- Grid and flex children generally require `min-width: 0`. Their default
  `min-width: auto` refuses to shrink below content, which is how one long
  label holds a whole row open.
- The shell's content column carries `min-width: 0`; without it the widest
  table widens the column and then the page.
- Responsive KPI layouts use auto-fit with a `minmax` track whose minimum
  cannot exceed the container — for example
  `repeat(auto-fit, minmax(min(100%, 200px), 1fr))`.
- Flexible search fields use a shrinkable track — `minmax(0, 1fr)` in grid, or a
  flex basis with `min-width: 0` — so the field yields width instead of pushing
  a trailing control off the page.
- Intrinsic controls (selects, buttons, date inputs) **should not** be
  stretched; they take their content width and wrap when the row runs out.
- Long labels, source names and badges **must** wrap or shrink safely. Long text
  children need `min-width: 0` and controlled wrapping.
- Severity badges remain content-sized and non-clipping: a badge refuses to
  shrink below its text, and a long sibling label wraps instead of squeezing it.
- Charts **must** remain contained by their parent layout at every supported
  width.

### Prohibited

- `overflow-x: hidden` on `html`, `body`, the app shell, or a page container to
  conceal a layout defect. It hides the symptom and leaves the cause, and it
  makes the acceptance condition above pass for the wrong reason.
- Absolute positioning as a general solution for label and badge layout. Use a
  flex wrapper with a natural `gap`, `min-width: 0` on the label side, and
  `flex: 0 0 auto` plus `white-space: nowrap` on the badge side.
- Severity-specific hardcoded widths, or any CSS keyed to a severity name.
- Fixed desktop widths that force viewport overflow.
- Clipping text to make a measurement pass.

---

## 10. Accessibility

The rules below are contracts with regression coverage. This repository has
**not** completed a formal WCAG audit, and no conformance level is claimed.

- Exactly one page-level `h1` per route, owned by `PageHeader`; sections start
  at `h2`.
- Sections, regions and interactive controls carry accessible names.
- Active navigation uses `aria-current="page"` (§8).
- Tabs expose valid, resolving tab-and-panel relationships.
- Disclosures use native semantics (§8).
- Status and severity meaning is available in text. **Color must never be the
  sole carrier of meaning** — this covers severity (`LOW`, `MEDIUM`, `HIGH`,
  `CRITICAL`), lifecycle state including `INSUFFICIENT_DATA`, and evidence and
  dimension states including `UNAVAILABLE`, `FAILED` and truncation. No two
  states in a set may reduce to the same words, and only a genuinely good
  outcome may take a positive tone.
- Tables use real column-header semantics, carry an accessible name or caption,
  and their sorting and pagination controls have names identifying the column
  and the action or state.
- Charts and timelines carry textual alternatives. The volume chart is exposed
  as a named image with a text summary of what it shows; an empty window leaves
  no named but meaningless image behind.
- Repeated interactive components generate unique IDs; ARIA references must
  resolve to elements that exist. A dangling `aria-controls` reads as present in
  the markup and absent to a screen reader.
- Screen-reader-only content must remain inside an appropriately positioned
  container (§8, `TableScroll`).
- Collapsed technical content **must not** be presented as visible initial
  evidence.

---

## 11. Testing expectations

- Test user-observable behavior and established semantics, not implementation
  details.
- Prefer accessible role and name queries over element selectors and class
  names.
- Use realistic fixtures, including hostile ones: long source names, every
  severity, and all three bucket completeness states.
- Ensure important regression tests are **non-vacuous**. Verify a new guard by
  breaking the behavior it protects and confirming the intended test fails, then
  restore the implementation immediately.
- Avoid large snapshots as the primary assertion strategy.
- Do not reproduce production algorithms in test code; a test that recomputes
  the thing it checks proves only that the code was copied correctly.
- Avoid assertions tied to harmless wording or class changes. A test that fails
  when a sentence is reworded pushes copy in the wrong direction — assert the
  invariant, not the phrasing.
- Use CSS contract tests **only** where a CSS property is itself the regression
  boundary, such as the `TableScroll` positioning contract.
- Page tests cover hierarchy and composition — section order, metric counts,
  cross-component semantics.
- Component and helper tests cover reusable semantics, and are the right layer
  for anything more than one route renders. Do not add a page test for an
  invariant a shared component test already proves completely.
- Evidence and lifecycle edge cases must be explicit, not incidental.
- Null versus zero, and the status distinctions in §4 and §10, require
  dedicated regression coverage.

External browser and viewport verification **complements** the jsdom suite and
must not be confused with it. jsdom does not lay out, so it can guard the
structure that lets CSS work but can never prove the absence of overflow; that
proof comes from measuring a real browser at the viewports in §9. Neither
substitutes for the other.

---

## 12. Change checklist

For use during code review of any UI change.

- [ ] Is the route's information hierarchy preserved (§3)?
- [ ] Are primary metrics still limited to four, with no fifth KPI?
- [ ] Are null, measured zero, `MISSING` and `PARTIAL` still distinct (§4)?
- [ ] Is active state derived from the explicit lifecycle state, never from
      `resolved_at` (§5)?
- [ ] Are evidence limitations — truncation, unavailable, failed — visible and
      distinguishable (§6)?
- [ ] Is contributor dimension diversity preserved (§6)?
- [ ] Is generated content deterministic and free of causal claims (§7)?
- [ ] Is technical detail progressively disclosed and collapsed by default?
- [ ] Is every meaning available without color (§10)?
- [ ] Are ARIA relationships valid, unique, and native semantics left intact?
- [ ] Is there page-level horizontal overflow at 1440, 1280 or 1024 — and was
      any overflow fixed structurally rather than hidden (§9)?
- [ ] Do the targeted tests and the full frontend gates pass?
- [ ] Was the change kept inside one coherent workstream, without unrelated
      fixes, dependency upgrades or scope expansion?
