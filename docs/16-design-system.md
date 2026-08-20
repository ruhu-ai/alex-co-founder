# 16 — Design System ("Workbench")

The visual and interaction system for the founder-facing surface
(`app/static/index.html`). One page, no framework, no build step — so the system
is enforced by **CSS custom properties plus a small set of component classes**,
not by a component library. Every rule here is implemented in the `<style>`
block of that file; this doc is the contract, that file is the runtime.

`adk web` is the dev inspection surface and is deliberately excluded — it is
never deployed and never styled.

---

## 0. Why this system exists

Three forces shape every decision below.

1. **The product's claim is trust.** The agent researches, drafts, and fills
   forms autonomously; the founder keeps judgment. The interface has to make the
   division of labour *visible* — what the agent did, on what evidence, and
   where a human decision is required. Ornament that obscures this is a bug.
2. **It is a dense operations surface, not a document.** Three live columns,
   polled every 5 s. It is scanned and operated. The craft is information
   design: state encoded in form as well as number, summary before detail.
3. **It is judged on a 4-minute video.** Legibility at video bitrate, in dark
   mode, at 1440×900, is a functional requirement — not a nicety.

### Design themes

| Theme | What it means in practice |
|---|---|
| **Night workbench, day desk** | Dark-first. Neutrals carry a deliberate blue bias (hue ≈ 228), never pure grey — the ground reads as a chosen material, not a default. Light theme is a genuine second design, not an inversion. |
| **Two voices: agent blue, founder amber** | Blue is *the agent acting*: links, agent affordances, primary actions, provenance. Amber is *you are needed*: approval gates, reason-required inputs, unfilled fields, adaptation notes. A founder can find their own obligations by colour alone. |
| **Evidence over ornament** | Timestamps, source citations, fit rationales, audit rows and version tags are first-class typography with their own tokens — not grey filler at the bottom of a card. |
| **Weight follows consequence** | Visual weight is proportional to irreversibility. Reading is quiet, editing is bordered, approving is a raised dialog with a distinct destructive treatment. Nothing irreversible ever looks like a routine button. |

---

## 1. Token architecture

Two layers. **Never use a primitive directly in a component rule.**

```
primitives (--c-blue-500, --c-neutral-900 …)   ← theme-independent hues
        ↓ resolved per theme
semantics (--surface-1, --ink-2, --accent …)   ← what components consume
```

Themes are selected three ways, and all three must resolve:

- bare `:root` — the **dark** palette (the app is dark-first and ships dark);
- `@media (prefers-color-scheme: light)` guarded `:root:not([data-theme="dark"])`
  — OS light preference wins when the user has made no explicit choice;
- `[data-theme="light"]` / `[data-theme="dark"]` — the toggle wins over the OS.

A colour must never be declared *only* inside a media query or `[data-theme]`
block. `body` sets an explicit `background` from a token.

The light palette is therefore written **twice** — once in the media block, once
in the toggle block — because CSS cannot share a declaration block across a
media query and a plain selector. That duplication is a genuine drift hazard
(divergence is invisible in one theme and glaring in the other), so
`scripts/check_contrast.py` parses both and fails the build if they disagree.

---

## 2. Colour

### 2.1 Semantic tokens

| Token | Role | Dark | Light |
|---|---|---|---|
| `--surface-0` | app ground (behind panels) | `#090B10` | `#EFF2F7` |
| `--surface-1` | panel | `#12151D` | `#FFFFFF` |
| `--surface-2` | inset / card inside a panel | `#191D27` | `#F5F7FC` |
| `--surface-3` | hover / pressed / selected | `#222735` | `#E7ECF6` |
| `--border` | card & panel boundary | `#353A4A` | `#C9D0DE` |
| `--divider` | hairline inside a grouped list | `#262B38` | `#E4E8F1` |
| `--field` | input / emphasis border | `#5D6475` | `#8D94A3` |
| `--ink-1` | primary text | `#E9ECF4` | `#161B26` |
| `--ink-2` | secondary text, card body | `#A6AEC2` | `#4E5769` |
| `--ink-3` | labels, timestamps, meta | `#7C8599` | `#646D80` |
| `--accent` | agent blue — graphic & fill | `#6E9BFF` | `#2F5BD0` |
| `--accent-ink` | agent blue as *text* | `#6E9BFF` | `#2F5BD0` |
| `--accent-on` | label on an accent fill | `#0A1020` | `#FFFFFF` |
| `--attn` | founder amber — graphic & fill | `#F0B341` | `#B8830F` |
| `--attn-ink` | founder amber as *text* | `#F3C36B` | `#8A5A06` |
| `--ok` | success — graphic | `#43D07C` | `#12924E` |
| `--ok-ink` | success as *text* | `#5FDC93` | `#0B7A41` |
| `--danger` | destructive — graphic & fill | `#FF6B61` | `#CE4136` |
| `--danger-ink` | destructive as *text* | `#FF8F87` | `#B8271C` |
| `--danger-on` | label on a danger fill | `#2A0906` | `#FFFFFF` |
| `--info` | workflow state — graphic | `#B58CFF` | `#8257E5` |
| `--info-ink` | workflow state as *text* | `#C4A4FF` | `#6440BE` |
| `--focus` | focus ring | `#8FB2FF` | `#2F5BD0` |

Tints (`--accent-bg`, `--attn-bg`, `--ok-bg`, `--danger-bg`, `--info-bg`) are
derived with `color-mix(in srgb, <hue> 14%, transparent)` so they stay correct
over whichever surface they land on.

### 2.2 The three-role rule (this is the fix for the old palette)

Every status hue exists in **three roles**, and they are not interchangeable:

- **graphic** (`--ok`) — bars, dots, stripes, meters. Non-text: floor **3:1**.
- **ink** (`--ok-ink`) — the same hue shifted for legibility as text. Floor **4.5:1**.
- **tint** (`--ok-bg`) — a low-alpha wash behind a badge. Carries no meaning alone.

The old system used one value for all three, which is why success/amber/danger
text failed contrast in light mode (measured 3.46 / 3.63 / 4.36 against white).

### 2.3 Fill policy

**Only `--accent` and `--danger` get solid fills.** Success, attention, state
and file-type marks are expressed as *tint + ink* only. A solid amber or green
button reads as low-quality chrome and cannot carry a 4.5:1 label without going
muddy — and a tinted mark keeps working when the theme flips, which a fixed
brand fill does not.

File-type marks (`--file-doc`, `--file-sheet`, `--file-slides`, `--file-pdf`)
are per-theme tokens for exactly this reason: the conventional Word-blue /
Excel-green hues are too dark on the dark ground and too light on the light one,
so each has a value per theme and is used as `color` over a 15% tint.

### 2.4 Contrast contract

Enforced by `scripts/check_contrast.py`, which fails CI on any regression.

| Pair | Floor |
|---|---|
| `--ink-1` on `--surface-1` / `-2` | 12:1 / 11:1 |
| `--ink-2` on `--surface-1` / `-2` | 7:1 / 6.5:1 |
| `--ink-3` on any surface incl. `--surface-0` | 4.5:1 |
| any `*-ink` on `--surface-1` / `-2` | 4.5:1 |
| `--accent-on` on `--accent`, `--danger-on` on `--danger` | 4.5:1 |
| `--border` on `--surface-1` / `-2` | 1.5:1 / 1.4:1 |
| `--field`, `--focus`, all graphic hues | 3:1 |

Current state: **31/31 pass in dark, 29/29 pass in light.**

### 2.5 Connector brand colours

Third-party brand hues (`#ea4335` Gmail, `#1a73e8` Drive …) arrive as data from
`GET /api/connectors` and are applied *only* to a 34 px monogram tile via
`--brand` / `--brand-soft`. They never touch text, borders, or fills elsewhere —
they are foreign material, quarantined to one element. Submission rules forbid
third-party logos, so marks are neutral geometric monograms, never logotypes.

---

## 3. Typography

### 3.1 Faces — three roles

No webfont is linked. The Cloud Run deployment must stay available and free for
judging until ~Oct 1, and a CDN font is a silent-fallback risk at video
recording time. The system stack is specified deliberately, not inherited.

| Token | Stack | Role |
|---|---|---|
| `--font-ui` | `ui-sans-serif, -apple-system, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", Inter, Roboto, sans-serif` | everything by default |
| `--font-display` | `"SF Pro Display", ui-sans-serif, -apple-system, "Segoe UI Variable Display", Inter, sans-serif` | brand, dialog and sheet titles, display numerals — set tighter |
| `--font-mono` | `ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Mono", Menlo, Consolas, monospace` | ids, audit rows, field names, anything the founder may need to read character-by-character |

Upgrade path (not taken in v1): vendor one variable face into
`app/static/vendor/fonts/` as `@font-face` with `font-display: swap`, exactly as
`vendor/pdfjs` is vendored. Do not add a CDN link.

### 3.2 Scale

Eight steps, integer pixels only. The old file used fourteen sizes including
`12.5px`, `13.5px`, `11.5px` and `10.5px` — fractional sizes rasterise
inconsistently and produce the "slightly off" feeling with no visible cause.

| Token | Size | Line-height | Use |
|---|---|---|---|
| `--fs-1` | 11px | 16px | badges, uppercase micro-labels, timestamps |
| `--fs-2` | 12px | 18px | captions, meta lines, audit detail |
| `--fs-3` | 13px | 20px | dense UI: card body, buttons, list rows |
| `--fs-4` | 14px | 22px | body, chat messages, inputs |
| `--fs-5` | 16px | 24px | lead paragraphs, dialog body |
| `--fs-6` | 18px | 26px | panel titles |
| `--fs-7` | 22px | 30px | sheet & dialog titles |
| `--fs-8` | 28px | 34px | display numerals, empty-state headlines |

### 3.3 Weight, tracking, numerals

- **400** body · **500** UI labels, buttons, card names · **600** headings,
  emphasis, dialog titles · **700** reserved for the brand and for numerals
  inside badges. Uppercase micro-labels are **600 + `--tr-label`**, never 700 —
  at 11px, 700 uppercase turns into a grey block.
- Tracking: `--tr-tight: -.015em` (≥18px) · `--tr-normal: 0` ·
  `--tr-label: .06em` (uppercase 11–12px).
- **`font-variant-numeric: tabular-nums` is mandatory** on every live-updating
  number — fit scores, day counters, `filled/total`, versions, counts, relative
  timestamps. The board repaints every 5 s; proportional digits make it twitch.
- Prose (chat, draft previews) caps at **68ch**; below that, long agent
  paragraphs become unreadable at full column width.
- `text-wrap: balance` on headings and empty-state copy.

---

## 4. Space, radius, elevation

**Space** — 4px base. `--sp-1:4 · 2:8 · 3:12 · 4:16 · 5:20 · 6:24 · 7:32 · 8:40 · 9:48`.
Sibling groups are laid out with flex/grid `gap`, never per-element margins.
The old file used 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18, 22, 26, 28.

**Radius** — six values: `--r-1:4 · 2:6 · 3:10 · 4:14 · 5:20 · --r-full:999px`.
Assignment is semantic: `--r-1` inline chips, `--r-2` inputs and small controls,
`--r-3` cards and buttons, `--r-4` panels, `--r-5` dialogs and sheets,
`--r-full` pills and avatars. The old file used thirteen values.

**Elevation** — three levels, theme-aware (dark relies on borders and low-alpha
black; light relies on soft blue-grey shadow):

| Token | Use |
|---|---|
| `--e-1` | raised card, doc card |
| `--e-2` | popover, toast, sticky header |
| `--e-3` | dialog, connectors sheet |

Scrims are `--scrim` with `backdrop-filter: blur(3px)`.

---

## 5. Iconography

**Phosphor Icons (MIT), `regular` weight** — vendored, not hand-drawn.

Icons were briefly hand-authored and that was the wrong call. Thirty-odd bespoke
marks is a maintenance liability with no upside here: this is a dense operations
tool where icon *legibility at 16px on a compressed video* is the requirement and
icon *personality* is not. A maintained family also carries optical corrections —
counter sizing, terminal alignment, pixel snapping — that hand-drawn geometry
does not get for free.

### 5.1 Delivery

The founder UI has no build step, so the sprite ships **inline** at the top of
`<body>` between `@sprite:begin` / `@sprite:end` markers. It is generated, not
written: `scripts/build_icons.py` reads the vendored subset in
`app/static/vendor/icons/phosphor/` and rewrites the block in place. The vendored
SVGs and the MIT licence are committed, exactly as `vendor/pdfjs` is — so nothing
is fetched at runtime, the deployment stays self-contained for judging, and
regeneration works offline.

Adding an icon is three steps: add a row to `ICONS` in the generator, drop the
Phosphor `assets/regular/<name>.svg` into the vendor directory
(`--list-missing` names the gaps), re-run the script.

### 5.2 Usage

```html
<svg class="ico" aria-hidden="true"><use href="#i-send"/></svg>
```

Phosphor draws filled paths on a 256 grid, so `.ico` sets `fill: currentColor`
and icons take colour from their parent — there is no stroke weight to keep in
sync. Sizes come from `--ico-sm:14 · --ico-md:16 · --ico-lg:20 · --ico-xl:24`;
icons are `flex: none` and optically centred against their label.

The semantic name is the API (`#i-send`, `#i-shield`), never the Phosphor name.
Swapping families later is a change to one map in the generator, not to markup.

### 5.3 Why not the glyphs it replaced

The previous UI mixed three incompatible systems — inline SVG at four stroke
weights (1.8 / 2 / 2.2 / 2.4), Unicode symbols (`⌘ ⟳ ☾ ☀ ▲ ✉ ◷ ◍ ◆ ⏸ ★ ⚑`) and
emoji (`📄 🎙 ⚠️`). Emoji and symbol glyphs render from whichever font the OS
picks, in colours the theme cannot control, at sizes the scale does not know
about — and the recording machine may not match the dev machine. `⌘` sat on the
Connections button as decoration, falsely implying a keyboard shortcut.

### 5.4 Inventory (42)

`send · mic · waveform · paperclip · chat · plug · refresh · sun · moon · plus ·
minus · close · search · chev-left · chev-right · board · clipboard · check ·
alert · flag · star · sparkle · clock · pause · shield · compass · target ·
pencil · trash · download · eye · file-doc · file-sheet · file-slides ·
file-pdf · cloud-up · inbox · calendar · globe · code · link · external`

Connector monograms map by connector `name` to a sprite symbol and fall back to
the backend's `icon` glyph for connectors the frontend does not yet know — so
adding a connector server-side still renders. Marks are neutral (`cloud-up` for
Drive, `inbox` for Gmail, `code` for GitHub), never logotypes: submission rules
forbid third-party logos.

## 6. Motion

| Token | Value | Use |
|---|---|---|
| `--dur-1` | 120ms | hover, press, colour change |
| `--dur-2` | 180ms | message entry, disclosure |
| `--dur-3` | 260ms | dialog and sheet entry |
| `--ease-out` | `cubic-bezier(.2,.8,.3,1)` | entering |
| `--ease-in-out` | `cubic-bezier(.4,0,.2,1)` | moving |

Motion is only used to explain a change of state: a new message arriving, a
dialog taking focus, the live-voice button holding attention while connected.
Nothing loops purely for decoration.

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important; animation-iteration-count: 1 !important;
    transition-duration: .01ms !important; scroll-behavior: auto !important;
  }
}
```

---

## 7. Components

Four parallel button systems (`button` / `.cbtn` / `.pill` / `.x`) and six
parallel chip systems (`.badge` / `.chip` / `.tag` / `.chip-ok` / `.doc-badge` /
`.cbadge`) collapse into two primitives with modifiers.

### 7.1 Button — `.btn[data-variant][data-size]`

| Variant | Fill | Label | Use |
|---|---|---|---|
| `primary` | `--accent` | `--accent-on` | the one forward action in a view |
| `secondary` | `--surface-2` + `--border` | `--ink-1` | equal-weight alternatives |
| `ghost` | transparent | `--ink-2` | header actions, toolbar |
| `quiet` | `--accent-bg` | `--accent-ink` | in-sheet actions (Connect, Ingest) |
| `danger` | `--danger` | `--danger-on` | reject, disconnect, anything destructive |

Sizes `sm` 26px · `md` 32px · `lg` 38px, all with `--r-3`; `icon` is a square of
the same height with `--r-full`. Minimum hit target 32×32; header and composer
controls are 34×34.

Every button carries `:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px }`.
The old file had **no focus styles at all** — keyboard operation was invisible.

### 7.2 Badge — `.badge[data-tone]`

Tones `neutral · accent · ok · attn · danger · info`. Rendered tint + ink,
11px/600/`--tr-label`, `--r-full`, tabular numerals. Deadline urgency maps
`≤3d → danger`, `≤10d → attn`, else `neutral`; overdue is `danger` with a solid
tint. Workflow state uses `info`.

### 7.3 Meter — `.meter` (fit score)

Value-encoded hue, replacing a bar that was green at every score: `<50 → --ink-3`,
`50–69 → --attn`, `≥70 → --ok`. Track `--surface-3`, 4px, `--r-full`,
`role="meter"` with `aria-valuenow`. The number sits beside the bar in tabular
numerals — the bar is redundant encoding, not the only encoding. The bar width
is clamped to 2–100% so a zero score is still visible and an out-of-range one
cannot overflow its track. Boundaries are pinned in
`tests/unit/test_design_encodings.py`.

### 7.4 Stepper — `.stepper`

The eight post-shortlist states as segments (`INTERVIEWING` → `FOLLOW_UP`):
done `--ok`, current `--accent` with a soft glow, future `--surface-3`.
Labelled underneath in `--fs-2`/600 with the founder-facing name from
`STATE_COPY`, not the raw enum. `aria-label` carries "Step 2 of 8 — Alex is
drafting".

### 7.5 Dialog — `.dialog`

One primitive for all three overlays (approval gate, PDF preview, connectors
sheet). Scrim + `--e-3` + `--r-5`, `role="dialog"`, `aria-modal`, focus moved to
the dialog on open and restored on close, Escape closes, click-outside closes.
Previously each overlay was hand-rolled and only one handled Escape.

**The approval gate is deliberately heavier than every other surface**: an
amber-ruled header, the program name and the irreversible consequence in
`--fs-5`, and the confirm button in `danger` — never in `primary`. Approving a
submission must never look like approving a draft section.

### 7.6 Card — `.card`

`--surface-2`, `--border`, `--r-3`, `--sp-3` padding. Hover raises the border to
`--field`; selected uses `--accent-bg` with an `--accent` border. Archived cards
carry a 2px `--ink-3` left rule and italic reason — an archived item is
*evidence of a decision*, so its reason is always visible, never truncated away.

### 7.7 Audit row — `.audit-item`

Mono `--fs-2`, three columns via grid (`actor` · `action + detail` · `when`) —
not a `float: right` timestamp. Result is a badge, not coloured text.
`--divider` between rows.

### 7.8 Empty states — `.empty`

Icon (20px, `--ink-3`) + one-line headline in `--fs-4`/600 + a `--fs-3`
explanation + the action that resolves it. The conversation column previously
rendered nothing at all until the first message — the first impression of the
product was a void where its main feature is.

---

### 7.9 Skeleton — `.skel`

First paint only. Skeletons are **markup, not state**: they sit in the HTML and
the first successful render replaces the container's `innerHTML`, so they are
destroyed rather than toggled and can never flash on the 5 s poll.

Each stands in for the shape it replaces — a board card is a title, a meta line
and a meter, so its skeleton is three lines at those widths. They carry
`aria-hidden="true"` inside a container marked `aria-busy="true"`, so assistive
tech announces "busy" rather than reading fake content; `markLoaded()` drops the
flag once real data lands.

A *failed* first load is the case that matters: there is no previous render to
preserve, so the skeletons resolve into "Can't reach the server" with a retry
button instead of shimmering indefinitely. After the first successful render the
opposite rule applies — the last good data stays on screen at full contrast,
because a blanked board during a blip reads as "everything was lost".

The `sheen` animation is frozen by the global reduced-motion guard, so that case
also drops the gradient and holds a flat resting tone.

## 8. Layout

```
header 52px (sticky, --e-2)
main   grid, gap --sp-3, padding --sp-3 --sp-4
       ├ board   clamp(300px, 24vw, 360px)
       ├ chat    minmax(420px, 1fr)
       └ review  clamp(340px, 27vw, 420px)
```

Panel height is `calc(100dvh - var(--header-h))` — `--header-h` is a token, not
a magic `53px`, and `dvh` keeps mobile browser chrome from clipping the composer.

Breakpoints — the old layout had none and broke below ~1150px:

| Width | Behaviour |
|---|---|
| ≥1280px | three columns as above |
| 960–1279px | board + chat; review becomes a right drawer opened from the header |
| <960px | single column, tabbed: Pipeline / Conversation / Review |

Every panel scrolls independently with `overscroll-behavior: contain`. Wide
content (fill reports, audit detail) gets its own `overflow-x: auto`; the body
never scrolls sideways.

---

## 9. Accessibility

- **Contrast** — §2.4, enforced in CI.
- **Focus** — `:focus-visible` on every interactive element, 2px `--focus`,
  2px offset. Dialogs trap and restore focus.
- **Semantics** — panels are `<section aria-labelledby>`; the chat log is
  `role="log" aria-live="polite"` so agent messages are announced; the toast is
  `role="status"`; the approval dialog is `role="dialog" aria-modal="true"`.
- **Colour is never the only channel** — every status badge carries text, the
  fit meter carries its number, the connector dot carries "Ready" / "Not
  connected".
- **Targets** — 32×32 minimum, 34×34 for composer and header.
- **Motion** — `prefers-reduced-motion` honoured globally.
- **Zoom** — layout holds to 200% because columns are `clamp()`-based.

---

## 10. Copy

Founder voice, from the founder's side of the screen. No agent jargon in the
UI — the state machine says `AWAITING_SUBMIT_APPROVAL`, the interface says
"Needs your approval".

| Instead of | Write |
|---|---|
| "no sections yet" | "Nothing drafted yet — Alex drafts once the interview fills the gaps." |
| "Feedback recorded — reject" | "Rejected. Alex will avoid this in the next draft." |
| "⚠️ Something went wrong" | "Alex couldn't reach the server. Your message wasn't sent — try again." |
| "reason (required)" | "Why? Alex learns from this." |

Buttons say what happens; the toast confirms in the past tense. No third-party
logos or product names beyond the connector names themselves (submission rule).

---

## 11. Acceptance checks

Guarded in CI (`.github/workflows/ci.yml`):

- [x] `scripts/check_contrast.py` passes — 29 pairs per theme, parsed from the
      shipped HTML so it tests what deploys.
- [x] The two light-palette blocks agree (same script; verified to fail on a
      seeded divergence).
- [x] `python scripts/build_icons.py` reproduces the committed sprite
      byte-for-byte.

Verified in-browser:

- [x] No fractional `font-size` and no size outside `--fs-1…8`, in both themes,
      across the whole page including the connectors sheet.
- [x] No hardcoded hex or `rgba()` in a component rule — colour lives in the
      token blocks and the quarantined connector `--brand` tiles only.
- [x] Every interactive element shows a `:focus-visible` ring (2px `--focus`,
      2px offset). Verify with real keyboard Tab: scripted `.focus()` does not
      trigger `:focus-visible` and reports a false negative.
- [x] No focus ring is clipped by an `overflow: hidden` ancestor.
- [x] Tab order reaches the approval dialog's buttons and returns on close;
      Escape closes the topmost dialog.
- [x] No emoji or Unicode symbol used as an icon; 42 sprite symbols, 0
      non-sprite SVGs.
- [x] Both themes render in all three selection states (bare `:root`, OS
      preference, explicit toggle).
- [x] Layout holds at 1440, 1280, 1100, 960 and 720 px with no sideways body
      scroll. **720px is also the 200%-zoom case** (a 1440 window at 200% is a
      720 CSS-px viewport) — that is what caught the header overflow.
- [x] `prefers-reduced-motion: reduce` parses, targets `*, ::before, ::after`,
      and collapses transitions from 120ms to 0.01ms when applied.
- [x] Every live-updating number uses tabular numerals.

Also guarded:

- [x] Encoding thresholds are pinned by `tests/unit/test_design_encodings.py`,
      which lifts `fitMeter` and `deadlineChip` out of the shipped page and runs
      them: fit bands at 49/50/69/70, deadline tones at 3/4/10/11 days, overdue,
      bar-width clamping, and the rule that the meter always renders its number
      (§9 — colour is never the only channel). Verified to fail on a seeded
      threshold change and on a seeded number-removal. Threshold assertions run
      even without Node, so the test is never silently vacuous.
- [x] First paint shows skeletons shaped like the content they stand in for;
      they are markup, so the first successful render destroys them and they
      cannot flash on the 5 s poll. A *failed* first load resolves them into a
      "Can't reach the server" state with a retry, rather than shimmering
      forever.

## 12. The mock portal is deliberately *not* this system

`mock_portal/` carries its own, separate visual identity — and that is the
point. On camera the form-filler drives it for a full minute of the demo. If it
shared Co-Founder's typography and palette, the sequence would read as a product
demoing against a prop it also designed. It has to look like somebody else's
software.

So it inverts every choice made here: warm paper (`#F6F4EF`) against our cool
blue-grey, a serif masthead against our system sans, institutional forest green
(`#14563D`) against our accent blue, square-ish 4px corners against our 10px.
Its palette is validated to the same floors (§2.4) — 17/17 pairs pass — but the
tokens are local to that file and share no names with ours.

Two details exist purely for the demo:

- **A filled field stays visibly filled.** Inputs carry a green tint and border
  once they hold a value, so the result of the fill is legible in a compressed
  video frame. This is scoped to `input[placeholder]:not(:placeholder-shown)` —
  an input with no `placeholder` attribute at all never matches
  `:placeholder-shown`, so an unscoped version marks *every* field as filled.
- **The confirmation reference is the largest thing on the receipt**, set in
  mono. It is the last frame of the demo and the one value a founder keeps.

### What the markup owes the form-filler

`services/browser_service.py` drives this page, so parts of the DOM are a
contract, not a style choice:

| Load-bearing | Why |
|---|---|
| every `name` and `type` attribute | `page.fill("[name='…']")` |
| `<label>` wrapping its control | recon reads `e.labels[0].innerText` |
| `<option>` text | `select_option(label=…)` |
| form `action` / `method` / `enctype` | submission + idempotency |

Consequently the required marker is a CSS `::after` on an inner `<span>`, never
a character in the DOM, and help text sits **outside** the `<label>` — otherwise
recon reads "Company name * As registered, including any suffix" as the field
name and the fill silently mismatches.
