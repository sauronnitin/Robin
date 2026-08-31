---
version: alpha
name: Veya
description: An honest job-application companion styled as printed matter — laid paper, iron-gall ink, and a single rubric red that marks attention rather than judgement.

colors:
  paper: "#E5E2D9"
  leaf: "#F1EFE9"
  raised: "#FAF8F3"
  rule: "#D3CEC1"
  rule-soft: "#E2DED2"
  ink: "#221C15"
  ink-secondary: "#5F574C"
  ink-muted: "#877F73"
  rubric: "#A82A1F"
  rubric-mark: "#C1352A"
  rubric-wash: "#EFE5DC"
  on-rubric: "#FAF8F3"
  ochre-ink: "#96570F"
  ochre-object: "#C1782A"
  paper-dark: "#100F0A"
  leaf-dark: "#17150F"
  raised-dark: "#201D15"
  rule-dark: "#332F25"
  rule-soft-dark: "#26231B"
  ink-dark: "#EDE8DE"
  ink-secondary-dark: "#B0A899"
  ink-muted-dark: "#847C6E"
  rubric-dark: "#E0614A"
  rubric-wash-dark: "#2A1A15"
  ochre-object-dark: "#D9944A"
  pigment-vermilion: "#C1352A"
  pigment-verdigris: "#0E7C55"
  pigment-ultramarine: "#33509E"
  pigment-orpiment: "#A8781C"
  pigment-tyrian: "#6A3D8F"
  pigment-terre-verte: "#7A8A1E"
  pigment-vermilion-dark: "#E0614A"
  pigment-verdigris-dark: "#17997A"
  pigment-ultramarine-dark: "#6C86DC"
  pigment-orpiment-dark: "#B68F22"
  pigment-tyrian-dark: "#A276D6"
  pigment-terre-verte-dark: "#8E9C2C"
  wash-1: "#ABA298"
  wash-2: "#857D71"
  wash-3: "#5F574C"
  wash-4: "#3C352C"
  wash-5: "#221C15"
  wash-1-dark: "#544C40"
  wash-2-dark: "#6D6455"
  wash-3-dark: "#8B8273"
  wash-4-dark: "#A9A092"
  wash-5-dark: "#CEC6B8"

typography:
  display:
    fontFamily: Newsreader, "Iowan Old Style", "Palatino Linotype", Georgia, serif
    fontSize: 56px
    fontWeight: 400
    lineHeight: 1.06
    letterSpacing: -0.018em
  h1:
    fontFamily: Newsreader, "Iowan Old Style", "Palatino Linotype", Georgia, serif
    fontSize: 34px
    fontWeight: 400
    lineHeight: 1.16
    letterSpacing: -0.012em
  h2:
    fontFamily: Newsreader, "Iowan Old Style", "Palatino Linotype", Georgia, serif
    fontSize: 24px
    fontWeight: 400
    lineHeight: 1.24
    letterSpacing: -0.008em
  h3:
    fontFamily: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace
    fontSize: 12px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0.12em
  body:
    fontFamily: Newsreader, "Iowan Old Style", "Palatino Linotype", Georgia, serif
    fontSize: 17px
    fontWeight: 400
    lineHeight: 1.62
  body-small:
    fontFamily: Newsreader, "Iowan Old Style", "Palatino Linotype", Georgia, serif
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace
    fontSize: 11px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0.1em
  data:
    fontFamily: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.3
    fontFeature: "tnum"
  figure:
    fontFamily: Newsreader, "Iowan Old Style", "Palatino Linotype", Georgia, serif
    fontSize: 40px
    fontWeight: 600
    lineHeight: 1.0
    letterSpacing: -0.02em
    fontFeature: "pnum"
  caption:
    fontFamily: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace
    fontSize: 11px
    fontWeight: 400
    lineHeight: 1.5

rounded:
  none: 0px
  sm: 2px
  md: 3px
  lg: 6px
  pill: 999px

spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  2xl: 32px
  3xl: 48px
  4xl: 64px

components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.raised}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 10px 18px
  button-primary-hover:
    backgroundColor: "{colors.rubric}"
    textColor: "{colors.on-rubric}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 10px 18px
  button-primary-disabled:
    backgroundColor: "{colors.rule-soft}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 10px 18px
  button-secondary:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 10px 18px
  input:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.data}"
    rounded: "{rounded.sm}"
    padding: 10px 12px
    height: 38px
  input-focus:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.data}"
    rounded: "{rounded.sm}"
    padding: 10px 12px
    height: 38px
  input-error:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.data}"
    rounded: "{rounded.sm}"
    padding: 10px 12px
    height: 38px
  card:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.ink}"
    typography: "{typography.body-small}"
    rounded: "{rounded.md}"
    padding: "{spacing.xl}"
  stat-tile:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.ink}"
    typography: "{typography.figure}"
    rounded: "{rounded.md}"
    padding: "{spacing.lg}"
  chip-sample:
    backgroundColor: "{colors.rubric-wash}"
    textColor: "{colors.rubric}"
    typography: "{typography.caption}"
    rounded: "{rounded.sm}"
    padding: 3px 7px
  table-header:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.rubric}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: 12px 16px
  nav-item-active:
    backgroundColor: "{colors.rubric-wash}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 8px 12px
---

# Veya Design System

## Overview

Veya applies for jobs on a person's behalf and tells them the truth about where they stand. Its users are job seekers who are usually anxious and frequently burned by tools that promised volume and delivered silence, so trust has to be earned in the first thirty seconds. The system is styled as **printed matter** — the register of the pamphlet, the declaration, the corrected proof — because every genuine upheaval in how people live arrived as ink pressed into paper and looked entirely ordinary while it happened.

The emotional target is *serious, unhurried, made*. This must never become a celebratory productivity app, never a cold enterprise console, and never a nostalgia exercise. It is a working document, not a keepsake.

Every value in this system was derived from a brand behaviour and then measured. Colour was computed with a palette validator rather than judged by eye, which caught five defects invisible on screen.

## Colors

The ground is laid rag paper, not cream — cooler and more mineral, because cream reads nostalgic and soft. `ink` is iron-gall, the ink of most constitutions worth naming: a very dark warm brown rather than black. No pure white and no pure black appear anywhere.

`rubric` (`#A82A1F`) is the system's only interface accent, and it carries the historical meaning of rubrication: it marks *what to attend to*, never *how you did*. There is deliberately **no success green** — the product refuses to let an action count as an outcome, so the palette refuses to congratulate. State is carried by icon, label, weight, and rule instead of by hue, which was already mandatory since colour alone may never encode meaning.

`ochre-object` (`#C1782A`) belongs exclusively to illustration and physical objects. It must never enter the interface layer, because vermilion against ochre measures **1.47:1** — full-colour readers cannot separate them. The domain split is what keeps both usable.

The six `pigment-*` tokens are the categorical chart palette, drawn from an illuminator's real pigment box. All six pass the OKLCH lightness band, chroma floor, CVD separation (worst adjacent ΔE 8.1 deutan), the normal-vision floor, and 3:1 contrast, in both modes. The `wash-*` ramp is sequential magnitude: one ink, thinned. Text colours meet WCAG AA — `ink` at 14.67:1, `ink-secondary` at 6.18:1, `rubric` at 6.06:1.

**Pigments are opt-in per screen, never automatic.** A screen earns them only when it carries genuinely categorical series that are not already distinguished by a direct label. If the marks are labelled, hue encodes nothing the text does not already encode, and spending it costs the monochrome discipline the whole printed-matter read depends on — use `wash-*` steps instead. The Dashboard is the worked example: sixteen tiles, one three-slot legend, every slot directly labelled, so it runs on ink and `rubric` alone and shows exactly one chromatic value in either mode.

## Typography

Two families only, and the pairing is the whole idea: an old-style serif on an ancient substrate, set against a monospace that reads as a precise instrument. There is no sans-serif in this system — the absence is deliberate, and adding one collapses the tension that makes the page feel both old and forward.

The serif (Newsreader, falling back to Iowan Old Style and Palatino) carries `display`, `h1`, `h2`, `body`, and `figure`. It does the editorial and philosophical work. The monospace carries `h3`, `label`, `data`, and `caption` — every label, every figure in a table, every chip, every piece of interface chrome. This mirrors the paper study directly, where every stage label was mono type on a paper tag.

`figure` is serif at weight 600 rather than the mono, because at tile scale a mono numeral reads as telemetry while a serif numeral reads as a considered statement.

**Figures are proportional; only columns are tabular.** `figure` carries `pnum` and `data` carries `tnum`. Tabular numerals give every digit the width of a zero, which is exactly right for a table column that must align vertically and exactly wrong for a standalone 40px number — `71` renders loose and gappy with a visible hole after the 7. Reserve `tnum` for table rows and axis ticks.

Section labels use wide tracking (`0.1em`+) at small sizes, which is how printed marginalia behaves.

## Layout

Spacing is a 4px-based scale from `xs` (4px) to `4xl` (64px), and density is **comfortable, not tight** — a page that crowds its content reads as anxious, which is the exact feeling the product exists to reduce.

The signature layout device is the **rubric rail**: a narrow left margin column, roughly 132px, carrying section numerals and labels in mono rubric red, beside a measured content column. This is how marginalia physically worked in manuscripts, and it gives every screen an obvious reading order. Below 760px the rail collapses above its content rather than shrinking.

Running prose holds to 60–66 characters. Data tables may run full width inside their own horizontally scrolling container, so the page body never scrolls sideways.

## Elevation & Depth

There are **no drop shadows in this system**. Depth comes from three warm surface steps — `paper` behind `leaf` behind `raised` — separated by 1px hairlines in `rule`. This follows directly from the *well-made* trait: layering is how physical paper actually stacks, and shadows would simulate a lighting model that paper on a desk does not have.

Hairlines do a great deal of work and should be treated as a primary structural element, not a fallback. Where a stronger division is needed, use a 2px `ink` rule rather than a heavier shadow or a darker fill.

## Shapes

Corners are close to sharp: `sm` (2px) is the default for controls, `md` (3px) for cards, `lg` (6px) only where something must read as physically distinct. Paper is cut, not moulded, so generous radii fight the substrate. `pill` exists solely for count badges and should stay rare.

Sharpness here signals precision and seriousness. The moment radii climb past 8px the system starts reading as a friendly consumer app, which contradicts the whole position.

## Components

`button-primary` is ink-filled with mono label type, and its hover state is one of the few places rubric red fills a surface — the colour arrives on intent, which is exactly rubrication's job. `button-secondary` is a `leaf` surface with a `rule` hairline. Disabled states drop to `rule-soft` with `ink-muted` text and never rely on opacity alone.

`input` sits on `raised` with a hairline; `input-focus` takes a 2px `rubric` ring, since focus is genuinely "attend to this here". `input-error` takes the same rubric treatment plus an icon and a written message — a genuine failure earns the loud colour, and the message is what carries the meaning.

**Card anatomy — five slots, fixed order.** Only the label is required; the rest are optional, but they never reorder and never swap places.

1. **label** — mono caps, tracked, `ink-muted`. Names what follows, which is the rubric's original job. An optional badge rides the same line, right-aligned.
2. **figure** — serif, proportional numerals, **exactly one per card**, with its unit set inline on the same baseline.
3. **subline** — serif sentence in `ink-secondary`. The plain-English reading of the figure.
4. **viz** — a full-width band on the card **floor**, never beside the figure.
5. **caption** — mono, `ink-muted`. The citation, the benchmark, or the caveat.

Two rules carry most of the weight. **One figure per card:** if a card wants a second number, it is two cards, or the second number belongs in the subline as prose. **The viz sits on the floor at full width:** pinning it with `margin-top: auto` lands every plot in a row on the same line regardless of how much prose sits above it, and that shared baseline is most of what makes a board look composed. Putting a plot in a corner *aside* creates two focal points inside a 150px card and the figure loses.

**A single ratio is a meter, never a two-slice ring.** The number is already printed several times larger beside it, so a donut restates it and competes with it. Rings are for part-to-whole with three or more segments, and even then a stacked split usually reads better at card width.

`card` and `stat-tile` share the `leaf` surface and hairline. `stat-tile` sets its number in `figure` (serif, tabular) with a mono `label` above it. `chip-sample` marks placeholder or estimated data and must survive unchanged from the current dashboard — showing the work is a value, not a nicety. `table-header` uses mono `label` type in rubric red, which is the rubric doing its original job: naming what follows.

## Do's and Don'ts

**Do**

- Keep exactly one loud thing per view. If two elements are red, neither is.
- Carry state with an icon, a label, and a weight — colour is the last channel, never the only one.
- Set every figure in a tabular numeral style so columns align.
- Use hairlines and surface steps for structure; let layering do what shadows would.
- Re-run the palette validator before introducing any colour. Three of the five defects it caught were invisible to the eye.
- Keep `ochre` in illustration and `rubric` in interface. They measure 1.47:1 against each other.

**Don't**

- Don't add a success green, a celebratory state, or a congratulatory message. Nothing here congratulates.
- Don't use red for staleness, waiting, or lateness. Silence is the expected outcome and gets neutral ink.
- Don't introduce a sans-serif. The serif/mono pairing is the identity, and a third family dissolves it.
- Don't add drop shadows, gradients, or radii above 8px — each one moves the system toward a generic consumer app.
- Don't cycle categorical pigments for a seventh series. Fold it into "Other" or switch to small multiples.
- Don't let text wear a pigment colour. The mark beside the number carries identity; the number stays in ink.
