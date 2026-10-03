<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-lockup-light.png">
    <img src="docs/brand/logo-lockup.png" alt="FinSight AI" width="260">
  </picture>
</p>

# FinSight AI — Design System

A single global stylesheet ([`src/styles/index.css`](src/styles/index.css)) drives the entire
UI through CSS custom properties (design tokens) and a library of reusable component
classes. Pages compose these classes; one-off styling is done with inline styles that
reference the same tokens (`var(--…)`). This keeps the look cohesive and makes a future
theme (e.g. dark mode) a token-only change.

## Tokens (`:root`)

| Group | Tokens | Notes |
|-------|--------|-------|
| **Brand** | `--blue` `--blue-strong` `--blue-soft` `--ai-teal` `--green` `--amber` `--red` `--purple` | `--blue` is the primary identity (now **indigo** `#4F46E5`; token name kept for compatibility). Teal (`#0D9488`) marks AI features; purple = escalated. |
| **Surfaces** | `--bg` `--surface` `--surface2` `--surface3` `--border` `--border-strong` | Layered neutrals, cool grey. |
| **Text** | `--text1` (primary) `--text2` (secondary) `--text3` (muted) `--text-muted` | WCAG-AA contrast on `--surface`. |
| **Semantic** | `--badge-*-bg/text`, `--alert-*-bg/text` | Status colors for badges & alerts. |
| **Typography** | `--font-sans` (Inter) `--font-mono` (DM Mono) + `--fs-xs … --fs-3xl` | Mono is reserved for IDs, numbers, and codes. |
| **Radius** | `--r-sm` 6 · `--r-md` 8 · `--r-lg` 12 · `--r-xl` 16 · `--r-pill` | |
| **Elevation** | `--shadow-xs/sm/md/lg`, `--ring` (focus) | Soft, layered shadows. |
| **Layout** | `--sidebar-w` `--topbar-h` `--content-pad` | Adjusted per breakpoint. |

**Rule of thumb:** never hard-code a hex color or a font family in a component — use a
token. Spacing/sizing may use raw px inline, but prefer the radius/shadow tokens.

## Core component classes

- **Layout:** `.app` `.sidebar` `.main` `.topbar` `.content`
- **Buttons:** `.btn` + `.btn-primary|secondary|ghost|danger|success|teal`, modifier `.btn-sm`
- **Cards:** `.card` `.card-sm` `.card-title` `.card-sub`, `.stat-card` (+`.stat-label/value/delta`)
- **Data:** `.table-wrap` > `table` (horizontal-scroll on small screens), `.badge-*`,
  `.progress-bar`/`.progress-fill`, `.tabs`/`.tab`
- **Forms:** `.form-group` `.form-label` `.form-input` (`select.form-input`/`.form-select`
  get a custom chevron), `.form-row` (2-up) `.form-row3` (3-up)
- **Feature blocks:** `.timeline`, `.pipeline`, `.memo-preview`, `.doc-slot` + OCR
  sub-classes, `.kv-summary`, `.alert-banner`, `.empty-state`
- **Grids:** `.grid2` `.grid3` `.grid4` `.grid5`, `.stats-grid`, `.tile-grid`
  (auto-fill cards), plus page-layout helpers `.detail-grid` / `.upload-grid` /
  `.kg-grid` (main + `.page-aside` sticky column).

Icons come from [`src/icons/Icons.jsx`](src/icons/Icons.jsx) (lucide-react). Use
`<Icon d={Icons.x} size={…} />` — do not use emoji as UI icons.

## Responsiveness

Mobile-first; breakpoints collapse layouts progressively:

| Breakpoint | What changes |
|------------|--------------|
| `≤ 1200px` | `--content-pad` unaffected; `grid5→3`, `grid4→2`, `stats-grid→2`. |
| `≤ 1024px` (tablet) | **Sidebar becomes a slide-over drawer** (hamburger in topbar + dimmed overlay). All two-column page layouts (`detail/upload/kg-grid`) and `.page-aside` go single-column. `grid3→2`. Padding tightens. |
| `≤ 768px` (mobile-lg) | All multi-column grids and `form-row(3)` → single column. Cards/headings shrink; search bar flexes. |
| `≤ 480px` (mobile) | `stats-grid→1`; topbar search + username hidden to declutter. |

The drawer state lives in [`Layout.jsx`](src/components/layout/Layout.jsx) (`navOpen`),
auto-closes on navigation, and is toggled by the `.menu-btn` in
[`Topbar.jsx`](src/components/layout/Topbar.jsx). `.menu-btn` is hidden ≥1024px via the
higher-specificity selector `.icon-btn.menu-btn`.

Wide tables stay in `.table-wrap` and scroll horizontally on small screens (never break
the page width). Touch targets (buttons, inputs) are ≥40px.

## Conventions for new pages

1. Start from `PageHeader` + `.card` blocks.
2. Use a `.gridN` / `.stats-grid` class for any multi-column row — **never** an inline
   `gridTemplateColumns: "repeat(N,1fr)"`, which won't collapse on mobile. For card
   galleries use `.tile-grid` or `repeat(auto-fill, minmax(min(100%, Npx), 1fr))`.
3. For a main+sidebar layout, use `.detail-grid`/`.upload-grid` with a `.page-aside`
   sticky column.
4. Reference tokens for all colors/typography.
