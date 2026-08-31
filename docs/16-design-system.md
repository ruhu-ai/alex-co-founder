# 16 — Design System ("Workbench")

The visual and interaction system for every founder-facing product surface,
including `app/static/index.html` and `app/static/hiring.html`. The original
runtime was one no-framework page whose `<style>` block owned these rules;
Hiring later added page-local approximations. That duplication is not the target
contract. All product routes must consume one shared/generated semantic-token
and component source (CSS custom properties plus the small component grammar),
with docs 36's shared rail/header/contextual-workspace composition. Similarly
named local variables, a second font stack, or copied navigation markup do not
constitute design-system reuse.

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
`GET /api/connectors` and are applied *only* to a 34 px mark tile via `--brand` /
`--brand-soft`. They never touch text, borders, or fills elsewhere — they are
foreign material, quarantined to one element. Submission rules forbid
third-party logos, so the marks themselves are generic (§5.5), never logotypes.

**Brand hues are chosen for a white page, so some are unreadable on ours.**
Measured against `--surface-1` in dark: GitHub `#1f2328` scores **1.16**, Slack
`#611f69` **1.66**, Jira `#0052cc` **2.68** — invisible, in the theme the demo
is recorded in.

The fix splits cleanly between the two things that know different halves of the
problem. Whether a hue is too dark is a property of the colour, so the UI tests
it at render time and stamps `data-dim`; *how much* to compensate depends on the
theme, so CSS owns it through one token:

```css
.cbadge[data-dim] { color: color-mix(in srgb, var(--ink-1) var(--brand-lift), var(--brand)); }
```

`--brand-lift` is **55%** in dark and **0%** in light — so the rule is a literal
no-op on white and the vendor's own colour is preserved untouched, which matters:
shifting a brand hue that is already legible is a needless deviation. After the
lift the three failures read 5.84 / 6.44 / 7.42.

Brand colours are backend data, so this cannot be checked at build time from the
stylesheet alone. `tests/unit/test_design_encodings.py` walks the catalog and
asserts every hue clears 3:1 after the theme-appropriate lift, that light stays
at 0%, and — guarding the guard — that at least one hue still trips the
threshold, so the test can never pass vacuously.

The implementation uses a standard relative-luminance test followed by a
theme-owned compensation token; no third-party assets are involved.

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

### 5.4 Inventory (51)

`send · mic · waveform · paperclip · chat · camera · screen-share · plug ·
refresh · sun · moon · plus ·
minus · close · search · chev-left · chev-right · board · clipboard · check ·
alert · flag · star · sparkle · clock · pause · shield · compass · target ·
pencil · trash · download · eye · file-doc · file-sheet · file-slides ·
file-pdf · cloud-up · inbox · at · calendar · globe · git-branch · hash ·
paper-plane · code · link · external · gear · sign-out · hiring`

### 5.4.1 Founder navigation and composer mapping

These mappings are binding for the shared Alex shell. They use conventional
Phosphor Regular shapes, one 20 px optical box in the rail and one 18 px optical
box in the composer; visible labels or accessible names/tooltips carry the full
meaning. Color never carries the action by itself.

| Surface action | Semantic sprite | Phosphor Regular source |
|---|---|---|
| New | `plus` | `plus` |
| Alex | `chat` | `chat-circle` |
| Search | `search` | `magnifying-glass` |
| Runs | `board` | `kanban` |
| Hiring | `hiring` | `users-three` |
| Decisions | `shield` | `shield-check` |
| Activity | `clock` | `clock` |
| Settings | `gear` | `gear` |
| Attach | `paperclip` | `paperclip` |
| Voice note | `mic` | `microphone` |
| Start/end live voice | `waveform` | `waveform` |
| Share camera | `camera` | `camera` |
| Share screen | `screen-share` | `monitor-arrow-up` |
| Send | `send` | `arrow-up` |

The globe remains Browser-only. A target is not a camera or a Hiring mark.
New alone is the filled blue/white rail CTA; the other rail and composer glyphs
inherit semantic foreground tokens. Disabled media controls retain their exact
accessible name and explain the unavailable state in their tooltip.

### 5.5 Connector marks

Real product logos would be better here — a connector picker is scanned by mark,
not by name, which is why every commercial one uses them. We do not, for one
reason: *"No third-party logos/ads in any submission material"*, and the UI
appears in the submission video. This is a rules constraint, not a claim that
generic marks read better.

Given that, the marks have to work harder, and **neutral is not a licence to
reuse**. Two rules, both enforced by
`tests/unit/test_design_encodings.py`:

- **One distinct mark per connector.** Gmail and IMAP were both `inbox`,
  separated only by brand tint — which vanishes on a greyscale display and
  survives compression poorly. IMAP is now `at`.
- **No connector wears an action icon.** Telegram was `send`, the composer's own
  submit arrow, so a connector looked like a button. It is now `paper-plane`.

| Connector | Mark | Why |
|---|---|---|
| Google Drive | `cloud-up` | company records, uploaded |
| Gmail | `inbox` | the one watched label |
| Email (IMAP) | `at` | any account — deliberately not a second envelope |
| Google Calendar | `calendar` | — |
| Browser | `globe` | the Playwright surface |
| GitHub | `git-branch` | repositories, not generic `code` |
| Slack | `hash` | Slack's own channel convention, generically drawn |
| Telegram | `paper-plane` | messaging, without borrowing `send` |
| Alex's Mailbox | `sparkle` | ours, not third-party, so the agent mark fits |
| Jira | `board` | — |

Brand hue still carries recognition, quarantined to the 34px tile (§2.5).
Unknown connectors fall back to the catalog's Unicode glyph so a server-side
addition still renders — but `test_every_connector_has_a_mark` fails the build
rather than letting that fallback ship silently.

Switching to real logos later is one map plus one vendored set: `CONN_ICON` in
`index.html` keys off the connector `name`, so no markup changes.

**Why the marks are first-party and generic.** Copying third-party marks would
create avoidable trademark and licensing ambiguity, while the submission rules
already forbid third-party logos. The product therefore owns the presentation
grammar—grouped inset rows, a brand-tinted tile, and the dark-mark lift in
§2.5—while every mark remains a deliberately generic icon from the checked-in
sprite.

### 5.6 The brand mark is not an icon

Until this section existed, `favicon.svg` carried Phosphor's `sparkle` — the
same path data `build_icons.py` compiles into the sprite as `#i-sparkle`. The
product's own mark and one of its toolbar buttons were the same glyph, and the
glyph was the four-point AI sparkle, on a product whose README opens by saying
it is not another chatbot. Everything below exists so that cannot recur.

**The mark ("Counterpart").** Two identical brackets on a 100-unit grid, drawn
14 units wide with round joins:

```
M20 80L20 20L64 20     the agent's half
M80 20L80 80L36 80     the founder's half
```

Rotating the pair 180° about (50, 50) maps each path onto the other: two parts,
neither subordinate, holding two sides of a frame that neither closes alone.
The artwork occupies 13…87 in both axes, so the drawn box is a square with
13 units of clear space already inside the viewBox.

**Amber means a decision is waiting — here too.** The halves are separate
strokes, so the founder's half can take `--attn` when something needs judgment,
turning the tab icon into the notification. That is the *only* sanctioned use
of a second colour in the mark; a decorative amber half would break §2.1.

**Generated, never hand-edited.** `scripts/build_brand.py` owns the geometry and
emits every asset from it — `brand/mark.svg`, `brand/lockup.svg`, `favicon.svg`,
the PNG icon set, `og-image.png`, `site.webmanifest`, and the `@brand:begin` /
`@brand:end` block in the three product pages. Same contract as
`build_icons.py`: change a constant, re-run, commit the output.

**It stays out of `ICONS`.** The mark must never be added to the sprite map in
`build_icons.py` — that is precisely how the sparkle ended up serving both
roles, and the next sprite rebuild would overwrite it.

**Clear space and minimum size.** Clear space is the 13-unit inset the viewBox
already carries; nothing else sits inside it. Minimum drawn size is 14 px — at
16 px in a favicon tile the interlock is still legible, below that it silts up.

**Layout is CSS, derived from the same constants.** The generated block ships
one rule set; a page sets `--brand-size` (the *drawn* mark height) and colour,
and the mark size, gap, and logotype height follow. An in-app lockup therefore
cannot drift from `brand/lockup.svg`.

| | |
|---|---|
| mark colour | `--accent` |
| logotype colour | `--ink-1` — single colour. The old `Co-<span>Founder</span>` split existed only because there was no mark to carry the accent |
| cap height ÷ drawn mark | 0.76 |
| gap ÷ drawn mark | 0.32 |

**The logotype ships as outlines.** It is set in IBM Plex Sans SemiBold
(SIL OFL 1.1) and converted to SVG paths at build time. No font file ships and
no webfont is linked — §3.1 forbids that — and outlining is also what fixes the
older bug: the wordmark used to be a `--font-display` CSS rule, so the brand's
letterforms were SF Pro, Segoe UI Variable, or Roboto depending on the visitor.

**The icon program.** `favicon.svg` (per-theme, rounded tile), a 180 px
`apple-touch-icon.png` (opaque and full-bleed; iOS applies its own radius),
192/512 PNGs for `site.webmanifest`, a maskable 512 whose mark is held to 52%
so it survives Android's circular crop at 80%, and a 1200×630 `og-image.png`.
All of them are listed in `PUBLIC_PATHS` in `app/auth.py`, because browser
chrome and link unfurlers fetch them with no session at all.

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
destroyed rather than toggled and can never flash on a later activity-driven
refresh or event-stream reconciliation.

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

### 7.10 Reference tabs — `.refzone` / `.rtab`

The review panel stacked five sections into **2190px of content in a 642px
column** — a 3.4-screen scroll, 73% of it the activity log. Tabs fix the length,
but tabs hide state, and this is the panel that carries the approval gate.

**The split is by consequence, not by category.** Anything the founder *acts on*
stays pinned above the bar: the approval gate, the stepper, the draft sections.
Only reference material is tabbed. Putting an irreversible decision behind a tab
would let a founder miss it, which is the single thing this panel exists to
prevent — so `test_approval_gate_is_never_inside_a_tabpanel` fails the build if
`#approvalSlot` ever moves into the zone.

Three tabs: **The form**, **Documents**, **Activity**. Result: the panel scrolls
**0px**; the log's 1628px scrolls inside its own pane.

**Labels carry state.** Each tab shows a count and, when something needs the
founder, an amber dot — so a *closed* tab can still ask for attention. The dot
becomes `--accent-on` on the selected tab so it stays legible against the fill.

Three things this got wrong first, all fixed by copy rather than CSS:

- **Four tabs did not fit.** At 318px every label truncated. "What Alex saw" and
  "Fill report" merged into "The form" — they were always one story about one
  object (what Alex saw of the form, then what it put in), and split across two
  tabs a founder checking "did it fill right?" had to look in two places.
- **"78" recon frames** as a count read like 78 items and named nothing the
  founder would act on.
- **"14/16"** was both wider than a closed tab and the wrong question. A closed
  tab has room for one number, so it shows the one you would act on: how many
  fields still need you. Nothing needed means no count. The full fraction lives
  in the pane.

The open tab gets `flex-grow: 1.4` — it carries its own count and dot, so it
needs more room than the closed ones.

**Keyboard**: standard ARIA tabs — `role="tablist"`/`tab`/`tabpanel`, roving
`tabindex` so the bar is one tab stop, ArrowLeft/Right/Home/End move selection
and focus together. Selection persists in `localStorage`.

## 8. Layout

```
header 52px (sticky, --e-2)
main   grid, gap 0, padding 0            ← full bleed
       ├ left cell  var(--left-w, clamp(300px, 24vw, 360px))   border-right: 1px --border
       │    ├ Pipeline surface (default)
       │    └ Browser surface (docs/18) — same cell, switched
       ├ chat    minmax(420px, 1fr)          border-right: 1px --border
       └ review  clamp(340px, 27vw, 420px)
```

**One workspace, not three cards.** The panels run edge to edge, separated by a
single hairline; each carries its own `--sp-3 --sp-4` padding so content never
touches a divider. The earlier treatment — floating rounded cards on the
`--surface-0` ground — implied three independent objects and spent roughly 56px
horizontal and 24px vertical on chrome, which a dense operations surface (§0)
cannot afford: the review panel gained 26px of usable height from this change
alone.

**The left cell is a switchable, resizable surface.** Pipeline and Browser
share it (never both at once); a ghost globe button in the header (top-left,
after the chips) toggles them, and the Browser surface auto-opens when a
browser run starts. The hairline between the left cell and the chat is a drag
handle (`role="separator"`, 10px hit area, keyboard arrows at 16px steps);
the width is clamped 260–600px and persisted in `localStorage`
(`leftPanelWidth`). The Browser surface follows the watch-view grammar:
toolbar (globe, typeable request URL field, Stop for every nonterminal browse
or fill run, close-back to Pipeline), a stage with the latest audited frame,
and a meta line. Entering a URL composes the normal audited `/wake` request; it
does not navigate the surface. The Browser remains an observation/control view,
not an interactive remote browser or iframe (18/22).

`--surface-0` therefore shows only *through* the dividers at full width. It is
still painted on `body` (§1) because it is the ground behind the drawer and any
overscroll.

The drawer below 1280px is the exception: it genuinely floats above the chat, so
it keeps a border, a left-side radius and `--e-3`. `#chat` drops its divider
there, since nothing sits to its right.

Panel height is `calc(100dvh - var(--header-h))` — `--header-h` is a token, not
a magic `53px`, and `dvh` keeps mobile browser chrome from clipping the composer.

Breakpoints — the old layout had none and broke below ~1150px:

| Width | Behaviour |
|---|---|
| ≥1280px | three columns as above; left cell switchable + draggable |
| 960–1279px | left cell + chat; review becomes a right drawer opened from the header |
| <960px | single column, tabbed: Pipeline / Conversation / Browser; review stays a header drawer; the drag handle and surface toggle hide (tabs cover them) |

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
- [x] The approval gate, stepper and draft sections are pinned outside every
      tabpanel; each tab has a pane and each pane a tab; the activity log scrolls
      inside its own pane. Verified to fail on a seeded gate-inside-a-pane move.
- [x] Every catalog brand hue clears 3:1 on the dark panel after the `--brand-lift`
      compensation, and light mode leaves vendor hues untouched — including a
      guard that the threshold is still tripped by something.
- [x] Every connector has a distinct, non-action sprite mark and no brand logo is
      vendored — four guards in `test_design_encodings.py`, each verified to fail
      on a seeded regression (including a broken glob path).
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
- [x] No emoji or Unicode symbol used as an icon; 51 sprite symbols, 0
      non-sprite SVGs.
- [x] Both themes render in all three selection states (bare `:root`, OS
      preference, explicit toggle).
- [x] Layout holds at 1440, 1280, 1100, 960 and 720 px with no sideways body
      scroll. **720px is also the 200%-zoom case** (a 1440 window at 200% is a
      720 CSS-px viewport) — that is what caught the header overflow.
- [x] Panels are full-bleed with hairline dividers and no outer padding; the
      sub-1280px drawer keeps its border, radius and shadow.
- [x] `prefers-reduced-motion: reduce` parses, targets `*, ::before, ::after`,
      and collapses transitions from 120ms to 0.01ms when applied.
- [x] Every live-updating number uses tabular numerals.

Also guarded:

- [ ] `index.html`, `hiring.html`, and every future founder-facing route consume
      the same shared/generated semantic token, typography, icon, focus,
      rail/header, card/tab/table/sheet, and responsive primitives. Tests fail
      on a page-local token/theme/font/navigation fork.
- [ ] Hiring's role/candidate renderers satisfy docs 36's shared shell and docs
      25's restricted domain contract without adding a separate visual product.

- [x] Encoding thresholds are pinned by `tests/unit/test_design_encodings.py`,
      which lifts `fitMeter` and `deadlineChip` out of the shipped page and runs
      them: fit bands at 49/50/69/70, deadline tones at 3/4/10/11 days, overdue,
      bar-width clamping, and the rule that the meter always renders its number
      (§9 — colour is never the only channel). Verified to fail on a seeded
      threshold change and on a seeded number-removal. Threshold assertions run
      even without Node, so the test is never silently vacuous.
- [x] First paint shows skeletons shaped like the content they stand in for;
      they are markup, so the first successful render destroys them and they
      cannot flash on later refresh/event reconciliation. A *failed* first
      load resolves them into a "Can't reach the server" state with a retry,
      rather than shimmering forever.
